// NOVA Profile Core -- Blink integration.
//
// This file is the *hook layer* of the NOVA profile engine.  It is
// deliberately placed *inside* Blink rather than in a JavaScript shim,
// because anything that runs in the JS world is observable:
//
//   - Function.prototype.toString() returns the shim's source
//   - Object.getOwnPropertyDescriptor() reveals an accessor
//   - A Proxy traps every property access
//   - navigator.webdriver is set to true
//
// The hooks are therefore called from *below* Blink, in the C++ accessors
// themselves, so that the page sees ordinary native properties: the same
// shape, the same enumerability, the same `toString()` output as a stock
// Android Chrome.
//
// The division of labour
// ---------------------------------------------------------------------------
//
// The patched accessors live in the Blink sources themselves:
//
//   third_party/blink/renderer/core/execution_context/navigator_base.cc
//   third_party/blink/renderer/core/frame/navigator_id.cc
//   third_party/blink/renderer/core/frame/navigator_ua.cc
//   third_party/blink/renderer/core/frame/navigator_ua_data.cc
//   third_party/blink/renderer/core/frame/navigator.cc
//   third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc
//   third_party/blink/renderer/core/frame/navigator_device_memory.cc
//   third_party/blink/renderer/core/frame/navigator_language.cc
//   third_party/blink/renderer/core/events/navigator_events.cc
//   third_party/blink/renderer/core/frame/screen.cc
//   third_party/blink/renderer/core/frame/local_dom_window.cc
//   third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc
//   third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc
//
// Each of those accessors keeps its *own* original body as the fallback:
// when the profile configuration has not been loaded, the accessor returns
// exactly the value it returned before the patch.  The stock behaviour is
// therefore the body of the accessor, not a call to a separate helper.
//
// This is deliberate.  The helpers this file used to call -- the
// ``NavigatorID::Standard*`` family, the ``NavigatorUAData::Default*``
// family and the ``Screen::Standard*`` family -- no longer exist in
// Chromium 159: Blink was reorganised, the accessors were moved into
// per-feature translation units, and the shared "standard value" helpers
// were dissolved into the accessors themselves.  Calling them would not
// link, and re-introducing them would put the stock value in a second
// place that the patched accessor could drift away from.
//
// So this file does *not* define the accessors.  It defines the
// configuration, the derivation of the per-surface noise keys, and the
// entry points the patched accessors call.  The accessor bodies and their
// fallbacks live in exactly one place: the Blink source.
//
// Every accessor reads from a single process-wide ProfileConfig, which is
// why the main frame, a cross-origin iframe, a sandboxed iframe, a Web
// Worker, a SharedWorker and a ServiceWorker all report identical values:
// they are separate JS worlds but the same native process.

#include "nova_core_config.h"
#include "nova_pixel_noise.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include "base/command_line.h"
#include "base/files/file_path.h"
#include "base/location.h"
#include "base/logging.h"
#include "base/strings/string_util.h"
#include "base/synchronization/lock.h"
#include "ui/gfx/geometry/rect_f.h"

namespace nova {

namespace {

// ---------------------------------------------------------------------------
// The process-wide configuration
//
// It is installed in the browser process before any child process is
// forked, and is inherited by the renderer, the GPU process and the
// network service through the Mojo shared memory region.
// ---------------------------------------------------------------------------

class NovaConfigHolder {
 public:
  static NovaConfigHolder& Get() {
    static auto* instance = new NovaConfigHolder();
    return *instance;
  }

  bool Initialize(const ProfileConfig& config) {
    base::AutoLock guard(lock_);
    config_ = std::make_unique<ProfileConfig>(config);
    // Each surface gets its own salt, so that a script which hashes two
    // surfaces and compares the results learns nothing about the link
    // between them.  Sharing the canvas salt between the canvas and the
    // WebGL surface would have made the two hash to the same thing for a
    // given profile -- a correlation a vendor can read straight off.
    canvas_key_ = DerivePixelNoiseKey(config.seed_config, "canvas2d",
                                      config.canvas_2d.salt,
                                      config.webgl.unmasked_renderer);
    webgl_key_ = DerivePixelNoiseKey(config.seed_config, "webgl",
                                     config.webgl.salt,
                                     config.webgl.unmasked_renderer);
    // The offscreen surface shares the canvas salt on purpose -- it is the
    // same kind of surface -- but the domain string separates the two
    // keys, so the offscreen pixels are not the canvas pixels.
    offscreen_key_ = DerivePixelNoiseKey(config.seed_config, "offscreen",
                                         config.canvas_2d.salt,
                                         config.webgl.unmasked_renderer);
    // The audio key uses the audio salt and the audio mode, so that the
    // audio fingerprint is independent of the canvas one.  A script that
    // compares the two hashes therefore learns nothing about the link
    // between the surfaces.
    audio_key_ = DerivePixelNoiseKey(config.seed_config, "audio",
                                     config.audio.salt, config.audio.mode);
    initialized_ = true;
    return true;
  }

  bool IsInitialized() {
    base::AutoLock guard(lock_);
    return initialized_;
  }

  // Loads the configuration on demand.
  //
  // The browser process installs the configuration before it forks any
  // child, but a renderer or the GPU process can reach this code first --
  // a renderer that starts before the browser's own initialization has run
  // would otherwise report the host's values for the very first request of
  // the very first page.  The load is therefore attempted lazily as well,
  // and it is idempotent.
  void EnsureInitialized() {
    {
      base::AutoLock guard(lock_);
      if (initialized_) {
        return;
      }
    }
    const base::CommandLine* command_line = base::CommandLine::ForCurrentProcess();
    if (!command_line) {
      return;
    }
    const std::string path = command_line->GetSwitchValueASCII("nova-config");
    if (path.empty()) {
      return;
    }
    ProfileConfig config;
    std::string error;
    if (!LoadProfileConfigFromCommandLine(&config, &error)) {
      LOG(ERROR) << "NOVA: failed to load the profile configuration: " << error;
      return;
    }
    Initialize(config);
  }

  ProfileConfig GetConfig() {
    base::AutoLock guard(lock_);
    return *config_;
  }

  // A reference to the live configuration.  The caller must not hold it
  // across a re-initialization, but the configuration is installed once
  // before any renderer exists and never replaced afterwards.
  //
  // The accessor exists because the client hints are read on every
  // request: copying the whole configuration for each of them would put
  // a measurable cost on the hot path.
  const ProfileConfig& GetConfigRef() {
    base::AutoLock guard(lock_);
    return *config_;
  }

  PixelNoiseKey CanvasKey() {
    base::AutoLock guard(lock_);
    return canvas_key_;
  }

  PixelNoiseKey WebGLKey() {
    base::AutoLock guard(lock_);
    return webgl_key_;
  }

  PixelNoiseKey OffscreenKey() {
    base::AutoLock guard(lock_);
    return offscreen_key_;
  }

  // The audio key.  It is derived from the audio salt and the audio mode
  // rather than from the canvas salt, so that a script comparing the
  // canvas hash with the audio hash cannot link them.
  PixelNoiseKey AudioKey() {
    base::AutoLock guard(lock_);
    return audio_key_;
  }

 private:
  NovaConfigHolder() = default;

  base::Lock lock_;
  bool initialized_ = false;
  std::unique_ptr<ProfileConfig> config_;
  PixelNoiseKey canvas_key_;
  PixelNoiseKey webgl_key_;
  PixelNoiseKey offscreen_key_;
  PixelNoiseKey audio_key_;
};

// Reads the configuration before the renderer process starts.
//
// This is called from content::ContentMainRunner::Initialize(), before the
// browser creates any renderer.  Nothing that a page can observe has
// happened at that point, so there is no window in which the page could
// see the real values.
bool InitializeNovaCore(const base::CommandLine& command_line) {
  const std::string path = command_line.GetSwitchValueASCII("nova-config");
  if (path.empty()) {
    return false;
  }

  ProfileConfig config;
  std::string error;
  if (!LoadProfileConfigFromCommandLine(&config, &error)) {
    LOG(ERROR) << "NOVA: failed to load the profile configuration: " << error;
    return false;
  }

  NovaConfigHolder::Get().Initialize(config);
  LOG(INFO) << "NOVA: profile " << config.profile_id << " loaded (preset "
            << config.seed_config.preset_id << ")";
  return true;
}

}  // namespace

// ---------------------------------------------------------------------------
// The public entry points used by the Chromium patches
// ---------------------------------------------------------------------------

// Called from content::ContentMainRunner::Initialize().
bool InitializeFromCommandLine(const base::CommandLine& command_line) {
  return InitializeNovaCore(command_line);
}

// Called from the GPU process when it starts, so that the GPU process
// holds the same configuration as the renderer.
bool InitializeGpuProcess(const base::CommandLine& command_line) {
  return InitializeNovaCore(command_line);
}

// Called from the network service when it starts.
bool InitializeNetworkProcess(const base::CommandLine& command_line) {
  return InitializeNovaCore(command_line);
}

bool IsNovaCoreInitialized() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  // The lazy load makes the accessor safe even in a process that was
  // started without the browser's own initialization having run.
  holder.EnsureInitialized();
  return holder.IsInitialized();
}

ProfileConfig GetNovaConfig() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  holder.EnsureInitialized();
  return holder.GetConfig();
}

// The same configuration, without the copy.  Use this on the hot paths --
// the client hints are read on every request.
const ProfileConfig& GetNovaConfigRef() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  holder.EnsureInitialized();
  return holder.GetConfigRef();
}

// ---------------------------------------------------------------------------
// The noise keys
//
// One key per readback surface.  The keys are derived from the profile's
// own seed, so the noise of a profile is reproducible across restarts
// and distinct from the noise of every other profile -- two profiles can
// therefore never collapse into the same canvas bucket.
// ---------------------------------------------------------------------------

PixelNoiseKey GetCanvasNoiseKey() {
  return NovaConfigHolder::Get().CanvasKey();
}

PixelNoiseKey GetWebGLNoiseKey() {
  return NovaConfigHolder::Get().WebGLKey();
}

PixelNoiseKey GetOffscreenNoiseKey() {
  return NovaConfigHolder::Get().OffscreenKey();
}

// ---------------------------------------------------------------------------
// The WebGL identity strings
//
// The unmasked renderer is served through WEBGL_debug_renderer_info.  It
// is patched at the WebGLContext level so that the value is identical in
// every context and cannot be distinguished from a real GPU by its
// lifetime or by the way it is exposed.
// ---------------------------------------------------------------------------

std::string GetUnmaskedVendorString() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().webgl.unmasked_vendor;
}

std::string GetUnmaskedRendererString() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().webgl.unmasked_renderer;
}

// ---------------------------------------------------------------------------
// The audio fingerprint
//
// The Web Audio API produces a device-specific fingerprint: an oscillator
// is run through a compressor and the resulting samples are hashed.  The
// values that shape that fingerprint -- the sample rate, the input and
// output latency and the channel count -- are served from the profile's
// own configuration, and the rendered samples are perturbed with the
// profile's audio key.
//
// The latency buckets are the ones Chromium's real Android implementation
// produces; a latency outside those buckets is itself a signal.
// ---------------------------------------------------------------------------

double GetAudioSampleRate() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return static_cast<double>(holder.GetConfig().audio.sample_rate);
}

double GetAudioBaseLatency() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().audio.base_latency;
}

double GetAudioOutputLatency() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().audio.output_latency;
}

int GetAudioChannelCount() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().audio.channel_count;
}

int GetAudioMaxChannelCount() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().audio.max_channel_count;
}

PixelNoiseKey GetAudioNoiseKey() {
  return NovaConfigHolder::Get().AudioKey();
}

void ApplyAudioNoise(float* samples, size_t count, size_t offset) {
  if (samples == nullptr || count == 0) {
    return;
  }
  if (!IsNovaCoreInitialized()) {
    return;
  }
  const ProfileConfig config = GetNovaConfig();
  if (config.audio.mode == "native_passthrough" ||
      config.audio.mode == "disabled") {
    return;
  }
  if (config.audio.respect_core_quantization &&
      config.audio.noise_strength <= 0.0) {
    return;
  }

  // The perturbation is derived from the noise key, so it is stable for
  // one profile and different for every other profile.  The magnitude is
  // far below the resolution of the Web Audio fingerprint -- the
  // fingerprint is a sum over the whole render, and the sum of the
  // perturbation cancels out -- but it is enough to move the last bits of
  // the result, which is exactly where the fingerprint lives.
  const PixelNoiseKey key = GetAudioNoiseKey();
  const double strength = config.audio.noise_strength > 0.0
                              ? config.audio.noise_strength
                              : 1.0;
  for (size_t i = 0; i < count; ++i) {
    const size_t index = offset + i;
    // A cheap integer mix of the key and the sample index.  It has to be
    // fast: this runs on every rendered quantum.
    uint64_t hash = static_cast<uint64_t>(key.bytes[0]) |
                    (static_cast<uint64_t>(key.bytes[1]) << 8) |
                    (static_cast<uint64_t>(key.bytes[2]) << 16) |
                    (static_cast<uint64_t>(key.bytes[3]) << 24) |
                    (static_cast<uint64_t>(key.bytes[4]) << 32) |
                    (static_cast<uint64_t>(key.bytes[5]) << 40) |
                    (static_cast<uint64_t>(key.bytes[6]) << 48) |
                    (static_cast<uint64_t>(key.bytes[7]) << 56);
    hash ^= index * 0x9E3779B97F4A7C15ULL;
    hash ^= hash >> 29;
    hash *= 0xBF58476D1CE4E5B9ULL;
    hash ^= hash >> 32;

    // The perturbation is scaled into the twenty-fourth bit of the
    // mantissa, which is below the level the Web Audio fingerprint
    // resolves but above the level of the quantisation that the audio
    // pipeline applies to a float.
    const double unit = static_cast<double>(hash >> 40) /
                        static_cast<double>(1ULL << 24) - 0.5;
    samples[i] += static_cast<float>(unit * strength * 1.0e-7);
  }
}

// ---------------------------------------------------------------------------
// The geometry perturbation
//
// The deltas are integer pixel offsets.  An integer offset keeps the
// layout, the painting and the hit testing consistent with each other: a
// fractional offset would move the rectangle without moving the pixels
// that belong to it.
// ---------------------------------------------------------------------------

int GetRectDeltaX() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().rects.delta_x;
}

int GetRectDeltaY() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().rects.delta_y;
}

int GetRectDeltaWidth() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().rects.delta_width;
}

int GetRectDeltaHeight() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().rects.delta_height;
}

// ---------------------------------------------------------------------------
// The media devices
//
// The device identifiers are derived from the profile's own salt, so two
// profiles never expose the same device ids.  A device id that repeats
// across two profiles is a stable identifier that links them.
// ---------------------------------------------------------------------------

std::string GetMediaDeviceSalt() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().media_devices.salt;
}

std::string RewriteMediaDeviceId(const std::string& device_id,
                                 const char* kind,
                                 size_t index) {
  if (!IsNovaCoreInitialized()) {
    return device_id;
  }
  const ProfileConfig config = GetNovaConfig();
  if (config.media_devices.mode == "native_passthrough" ||
      config.media_devices.mode == "disabled") {
    return device_id;
  }
  const std::string salt = config.media_devices.salt;
  if (salt.empty()) {
    return device_id;
  }

  // The host's identifier is mixed with the profile's own salt and the
  // position of the device in the list.  The same host, the same device
  // and the same position therefore produce the same identifier inside
  // one profile and a different one in every other profile, which is
  // exactly the property the surface needs.
  uint64_t hash = 1469598103934665603ULL;  // the FNV-1a offset basis
  const std::string material = salt + "|" + (kind != nullptr ? kind : "") +
                               "|" + std::to_string(index) + "|" + device_id;
  for (unsigned char c : material) {
    hash ^= c;
    hash *= 1099511628211ULL;
  }

  // The identifier keeps the shape of a UUID, because the Android camera
  // identifiers that a page expects to see have that shape and a page
  // that validates the format would reject anything else.
  char buffer[37];
  snprintf(buffer, sizeof(buffer),
           "%08x-%04x-%04x-%04x-%012llx",
           static_cast<unsigned int>(hash & 0xFFFFFFFFULL),
           static_cast<unsigned int>((hash >> 32) & 0xFFFFULL),
           static_cast<unsigned int>((hash >> 48) & 0xFFFFULL),
           static_cast<unsigned int>((hash >> 16) & 0xFFFFULL),
           static_cast<unsigned long long>(hash));
  return std::string(buffer);
}

// ---------------------------------------------------------------------------
// The motion sensors
//
// The readings are served from the profile's own configuration.  A device
// that is not moving still reports a little jitter, and the gravity
// reading has to agree with the accelerometer.
// ---------------------------------------------------------------------------

double GetSensorAccelerometerX() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().sensors.accelerometer.x;
}

double GetSensorAccelerometerY() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().sensors.accelerometer.y;
}

double GetSensorAccelerometerZ() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().sensors.accelerometer.z;
}

double GetSensorGyroscopeX() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().sensors.gyroscope.x;
}

double GetSensorGyroscopeY() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().sensors.gyroscope.y;
}

double GetSensorGyroscopeZ() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().sensors.gyroscope.z;
}

double GetSensorGravityZ() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.GetConfig().sensors.gravity.z;
}

std::string GetSensorSalt() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().sensors.salt;
}

// ---------------------------------------------------------------------------
// The storage estimate
//
// ``navigator.storage.estimate()`` returns the quota and the usage.  The
// usage has to stay below the quota; a site that sees otherwise knows
// the values are fabricated.
// ---------------------------------------------------------------------------

uint64_t GetStorageQuotaBytes() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().storage_network.quota_bytes;
}

uint64_t GetStorageUsageBytes() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.GetConfig().storage_network.usage_bytes;
}

std::string GetStorageSalt() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().storage_network.salt;
}

// ---------------------------------------------------------------------------
// The WebGPU adapter
// ---------------------------------------------------------------------------

std::string GetWebGpuSalt() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().webgpu.salt;
}

// ---------------------------------------------------------------------------
// The font inventory
// ---------------------------------------------------------------------------

bool IsFontInProfile(const std::string& family_name,
                     const std::vector<std::string>& installed) {
  // The generic families are always available: the CSS font matching
  // resolves them to whatever the platform offers, and a page that asks
  // for "sans-serif" is asking for a family, not for a file.
  static const char* kGenericFamilies[] = {
      "serif",      "sans-serif", "monospace",   "cursive",
      "fantasy",    "system-ui",  "ui-serif",   "ui-sans-serif",
      "ui-monospace", "ui-rounded", "math",       "emoji",
      "fangsong",
  };
  for (const char* generic : kGenericFamilies) {
    if (base::EqualsCaseInsensitiveASCII(family_name, generic)) {
      return true;
    }
  }

  // The comparison is case insensitive, because the CSS font matching is
  // case insensitive for the family names.
  for (const std::string& candidate : installed) {
    if (base::EqualsCaseInsensitiveASCII(candidate, family_name)) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// The geometry perturbation
//
// The perturbation is applied only when the profile asked for it.  A
// profile in the "noise" mode gets the deltas of its own seed; a profile
// in the "disabled" mode gets the rectangle the layout produced, so the
// page sees the host geometry untouched.
// ---------------------------------------------------------------------------

void ApplyRectDeltas(gfx::RectF& rect, const char* surface) {
  if (!IsNovaCoreInitialized()) {
    return;
  }
  const ProfileConfig config = GetNovaConfig();
  if (config.rects.mode == "disabled" || config.rects.mode == "native_passthrough") {
    return;
  }
  const int delta_x = GetRectDeltaX();
  const int delta_y = GetRectDeltaY();
  const int delta_width = GetRectDeltaWidth();
  const int delta_height = GetRectDeltaHeight();
  if (delta_x == 0 && delta_y == 0 && delta_width == 0 && delta_height == 0) {
    return;
  }
  // The surface name keeps the element rectangle and the range rectangle
  // from moving by exactly the same amount.  A page that compares the
  // two -- a classic cross-surface consistency check -- must not find
  // them trivially equal.
  int surface_shift = 0;
  if (surface != nullptr) {
    for (const char* c = surface; *c != '\0'; ++c) {
      surface_shift = (surface_shift * 31 + static_cast<unsigned char>(*c)) & 0xFF;
    }
  }
  const int shift_x = (surface_shift % 3) - 1;
  const int shift_y = ((surface_shift >> 3) % 3) - 1;

  rect.Offset(delta_x * (1 + shift_x) * 0.5f, delta_y * (1 + shift_y) * 0.5f);
  if (delta_width != 0 || delta_height != 0) {
    rect.set_width(std::max(0.0f, rect.width() + delta_width));
    rect.set_height(std::max(0.0f, rect.height() + delta_height));
  }
}

// ---------------------------------------------------------------------------
// The TLS handshake
//
// The lists are returned by value through a function-local static, so
// that the caller can hold on to the reference.  The configuration is
// read once and then never changes, so the copy is made once.
// ---------------------------------------------------------------------------

namespace {

// The lists are returned through a function-local static, so that the
// caller can hold on to the reference.  The configuration is read once
// and then never changes, so the copy is made once.
template <typename Reader>
const std::vector<std::string>& TlsList(Reader reader) {
  static const std::vector<std::string> kEmpty;
  static thread_local std::vector<std::string> cached;
  if (!IsNovaCoreInitialized()) {
    return kEmpty;
  }
  const ProfileConfig config = GetNovaConfig();
  cached = reader(config);
  return cached;
}

}  // namespace

const std::vector<std::string>& GetTlsCipherSuites() {
  return TlsList(
      [](const ProfileConfig& c) { return c.tls.cipher_suites; });
}

const std::vector<std::string>& GetTlsSupportedGroups() {
  return TlsList(
      [](const ProfileConfig& c) { return c.tls.supported_groups; });
}

const std::vector<std::string>& GetTlsSignatureAlgorithms() {
  return TlsList(
      [](const ProfileConfig& c) { return c.tls.signature_algorithms; });
}

const std::vector<std::string>& GetTlsAlpnProtocols() {
  return TlsList(
      [](const ProfileConfig& c) { return c.tls.alpn_protocols; });
}

const std::vector<std::string>& GetTlsVersions() {
  return TlsList([](const ProfileConfig& c) { return c.tls.versions; });
}

const std::vector<std::string>& GetTlsExtensionOrder() {
  return TlsList(
      [](const ProfileConfig& c) { return c.tls.extension_order; });
}

bool GetTlsGreaseEnabled() {
  if (!IsNovaCoreInitialized()) {
    return true;
  }
  return GetNovaConfig().tls.grease_enabled;
}

}  // namespace nova
