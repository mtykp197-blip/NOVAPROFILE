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

#include "base/base64.h"
#include "base/command_line.h"
#include "base/containers/span.h"
#include "base/files/file_path.h"
#include "base/files/file_util.h"
#include "base/location.h"
#include "base/logging.h"
#include "base/strings/string_util.h"
#include "base/synchronization/lock.h"
#include "ui/gfx/geometry/rect_f.h"
#include "third_party/blink/renderer/core/nova/nova_transport.h"

namespace nova {

// The name of the switch that carries the payload to the renderer.
//
// The payload is the profile's values without the code that installs
// them.  The code is compiled into the browser; only the payload
// travels.
extern const char kJsPayloadSwitchName[];

// The layer's code.
//
// It is the same for every profile and it is compiled into the browser,
// so it does not travel at all.
const std::string& GetJsRuntimeCode();

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
    //
    // The unmasked renderer goes into the key as well, for the same
    // reason the canvas key carries it: two profiles that differ only
    // in their GPU would otherwise carry the same audio fingerprint,
    // and the pair of them -- the GPU string and the audio hash -- is
    // a stronger identifier than either is on its own.
    audio_key_ = DerivePixelNoiseKey(config.seed_config, "audio",
                                     config.audio.salt,
                                     config.audio.mode + "|" +
                                         config.webgl.unmasked_renderer);
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
  // The profile reaches this process in one of two ways.
  //
  // The browser process is given a path: --nova-config.  It reads the
  // file and hands the document to the renderers.
  //
  // The renderer is given the document itself: --nova-config-source.
  // It is sandboxed and cannot open a file in the user data directory,
  // so a path would reach a process that cannot use it -- and a process
  // that cannot read the profile reports the host's values, which is
  // exactly what a site is looking for.
  //
  // Both are accepted here.  Checking only for the path would make the
  // renderer skip the configuration entirely, because the path is not
  // what it was given.
  const std::string path = command_line.GetSwitchValueASCII("nova-config");
  const std::string inline_source =
      command_line.GetSwitchValueASCII(kConfigSourceSwitchName);
  if (path.empty() && inline_source.empty()) {
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
// The surfaces the native core owns
// ---------------------------------------------------------------------------
//
// The JavaScript layer is the fallback for the surfaces the native core
// does not reach.  Where the native core *does* reach a surface, the
// native value is the authoritative one: the browser reports it from
// inside the process, with the stock descriptor shape, and no page can
// tell that anything was done at all.
//
// A JavaScript override on top of that would be strictly worse.  It
// would replace a value the page cannot detect having been replaced
// with one it can, and if the two ever disagreed the page would see
// the disagreement -- which is exactly the kind of inconsistency that
// gives a fingerprinting script something to key on.
//
// So the native core publishes the list of surfaces it owns, and the
// JavaScript layer stands down on exactly those.
//
// The list is derived from what the configuration actually carries,
// not from a fixed set: a profile that does not describe a surface
// leaves that surface to the JavaScript layer, and a profile that does
// describe it hands it to the native core.
std::vector<std::string> GetNativeOwnedSurfaces() {
  std::vector<std::string> surfaces;
  if (!IsNovaCoreInitialized())
    return surfaces;

  const ProfileConfig& config = GetNovaConfigRef();

  // The navigator surface.
  if (!config.navigator.user_agent.empty() ||
      !config.navigator.platform.empty() ||
      !config.navigator.languages.empty()) {
    surfaces.push_back("navigator");
  }

  // The screen surface and the window geometry.
  if (config.screen.width > 0 || config.screen.height > 0) {
    surfaces.push_back("screen");
    surfaces.push_back("viewport");
  }

  // The client hints, which the native core builds in one place so that
  // the low- and the high-entropy values cannot disagree.
  if (!config.user_agent_data.brands.empty()) {
    surfaces.push_back("user_agent_data");
  }

  // The surfaces the native core reads back with noise.
  //
  // Only the surfaces the native core actually patches are named here.
  // A surface it does not patch must not be named: the JavaScript
  // layer stands down on the surfaces that are named, so naming one
  // it does not patch would leave that surface reporting the host's
  // values.
  //
  // The WebGL identity strings are patched at the WebGLContext level,
  // so that the unmasked renderer is identical in every context and
  // cannot be told apart from a real GPU by its lifetime or by the way
  // it is exposed.
  if (!config.webgl.unmasked_vendor.empty() ||
      !config.webgl.unmasked_renderer.empty()) {
    surfaces.push_back("webgl");
  }

  // The font list is served from the font face set, so that a page
  // cannot tell it from the host's by the way it is enumerated.
  if (!config.fonts.installed.empty()) {
    surfaces.push_back("fonts");
  }

  return surfaces;
}

// The declaration the JavaScript layer reads to learn which surfaces the
// native core owns.
//
// It is a plain assignment to a property on the window that does not
// exist otherwise.  A page that does not know about it sees one more
// own property on the window -- which the JavaScript layer would have
// added anyway with its own marker.
//
// The property is only written when the browser was started with the
// expose switch.  Without it the declaration is empty, and the layer
// has to assume that the native core owns nothing: it then patches
// every surface itself, which is what a browser that was not patched at
// all needs.
//
// The switch exists because the property is a fingerprint of its own.
// A script that looks for names like this one finds it with a single
// property enumeration, and a browser that carries the profile's
// fingerprint must not carry a marker that says it does.
std::string BuildNativeSurfaceDeclaration() {
  // The switch is read once.  It is a debugging aid, and it is read on
  // a path that runs once per document.
  static const bool expose = base::CommandLine::ForCurrentProcess()
                                 ->HasSwitch("nova-expose-surfaces");

  if (!expose)
    return std::string();

  const std::vector<std::string> surfaces = GetNativeOwnedSurfaces();
  std::string json = "[";
  for (size_t i = 0; i < surfaces.size(); ++i) {
    if (i)
      json += ",";
    json += "\"" + surfaces[i] + "\"";
  }
  json += "]";

  return "window.__NOVA_NATIVE_SURFACES__ = " + json + ";\n";
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
  const ProfileConfig& config = GetNovaConfigRef();
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
  const ProfileConfig& config = GetNovaConfigRef();
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
  const ProfileConfig& config = GetNovaConfigRef();
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
//
// The reference is taken rather than a copy.  The TLS handshake reads
// these lists on every connection, and a copy of the whole
// configuration -- which carries the font list, the codec list and the
// device catalogue -- is far more than the handshake needs.
template <typename Reader>
const std::vector<std::string>& TlsList(Reader reader) {
  static const std::vector<std::string> kEmpty;
  static thread_local std::vector<std::string> cached;
  if (!IsNovaCoreInitialized()) {
    return kEmpty;
  }
  cached = reader(GetNovaConfigRef());
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
  return GetNovaConfigRef().tls.grease_enabled;
}

// ---------------------------------------------------------------------------
// The JavaScript fallback layer.
// ---------------------------------------------------------------------------

namespace {

// The payload, as the renderer receives it over the Mojo channel.
//
// The channel is the transport for the payload.  It has no length limit
// and it is not readable from another process, which the command line
// is.
std::string* const g_js_payload_from_mojo = new std::string();

// The layer's script, as the renderer assembles it.
//
// It is the code -- which is compiled into the browser and is the same
// for every profile -- followed by the payload, which carries the
// profile's values.
//
// The payload is read from the command line.  The command line is built
// when the renderer process is created, so the payload is there before
// the renderer runs a single line.  The Mojo channel arrives later:
// the renderer has to connect before it can deliver anything, and a
// navigation can begin before it does.  A renderer whose first
// navigation has no payload reports the host's values for every axis,
// which is the one thing the engine exists to prevent.
const std::string& JsRuntimeSource() {
  static const std::string* source = new std::string([] {
    const base::CommandLine* command_line =
        base::CommandLine::ForCurrentProcess();
    if (!command_line) {
      return std::string();
    }

    // The switch carries the payload, base64 encoded.  A Windows
    // command line cannot describe a document that contains quotes,
    // spaces and newlines, so the browser encodes it before it hands
    // it over; base64 has none of them and survives the trip intact.
    std::string payload;
    const std::string inline_encoded =
        command_line->GetSwitchValueASCII(kJsPayloadSwitchName);
    if (!inline_encoded.empty()) {
      if (!InflateFromCommandLine(inline_encoded, &payload)) {
        LOG(ERROR) << "NOVA: the JavaScript payload on the command line is "
                      "not valid base64";
        return std::string();
      }
    }

    // The payload that arrived over the Mojo channel wins over the one
    // on the command line: it is the same payload, but it did not pass
    // through the process table.
    if (!g_js_payload_from_mojo->empty()) {
      payload = *g_js_payload_from_mojo;
    }

    if (payload.empty()) {
      return std::string();
    }

    // The payload installs itself and the code reads it.  The code goes
    // first: it defines the marker the payload checks, and it reads the
    // payload as it starts.
    return payload + "\n" + GetJsRuntimeCode();
  }());
  return *source;
}

}  // namespace

// Called by the renderer when the browser hands the payload over the
// Mojo channel.
//
// The payload that arrives here is the whole payload, so it wins over
// anything the command line carried.
void SetJsRuntimeFromMojo(std::string runtime) {
  if (getenv("NOVA_TRACE")) {
    FILE* t = fopen("C:\\nova-test\\nova_trace.txt", "a");
    if (t) {
      fprintf(t, "mojo: runtime len=%d\n", (int)runtime.size());
      fflush(t);
      fclose(t);
    }
  }
  *g_js_payload_from_mojo = std::move(runtime);
}

const std::string& GetJsRuntimeSource() {
  if (!g_js_payload_from_mojo->empty()) {
    static const std::string* source =
        new std::string(*g_js_payload_from_mojo + "\n" + GetJsRuntimeCode());
    return *source;
  }
  return JsRuntimeSource();
}

bool HasJsRuntime() {
  return !GetJsRuntimeSource().empty();
}

// The script the renderer actually runs.
//
// It is the JavaScript layer with the native core's declaration of what
// it owns in front of it.  The declaration has to run first: the layer
// reads it as it starts and stands down on the surfaces the native core
// has already taken care of.
//
// The result is *not* cached.  The script arrives over a Mojo channel,
// and the first navigation can start before the channel has delivered
// it; a value that was computed once and kept would then be the empty
// one, for the rest of the renderer's life.  Recomputing costs a string
// concatenation, and this runs once per document.
const std::string& GetEffectiveJsRuntimeSource() {
  static thread_local std::string source;
  const std::string& runtime = GetJsRuntimeSource();
  if (getenv("NOVA_TRACE")) {
    FILE* t = fopen("C:\\nova-test\\nova_trace.txt", "a");
    if (t) {
      fprintf(t, "effective: runtime len=%d\n", (int)runtime.size());
      fflush(t);
      fclose(t);
    }
  }
  if (runtime.empty()) {
    source.clear();
    return source;
  }
  if (!IsNovaCoreInitialized()) {
    source = runtime;
    return source;
  }

  // The declaration names only the surfaces the native core owns.
  // When it owns none of them -- which is the case for a browser that
  // was not patched at all -- the layer is left to do everything.
  const std::string declaration = BuildNativeSurfaceDeclaration();
  source = declaration + runtime;
  return source;
}

const char kJsPayloadSwitchName[] = "nova-js-payload-source";

// ---------------------------------------------------------------------------
// The browser-side hand-over cache
// ---------------------------------------------------------------------------
//
// The browser process reads the profile and the JavaScript layer from disk
// and puts both on the command line of every renderer it spawns.  The
// renderer cannot do that read itself: it is sandboxed and the user data
// directory is not among the paths it may open.
//
// The catch is *where* the browser-side read happens.  The renderer command
// line is built on the browser's IO thread, and a thread that has been
// marked as non-blocking must not wait on the disk: a stat() or a read()
// there trips the "tls_blocking_disallowed" DCHECK and takes the whole
// browser down.
//
// So the read happens exactly once, on the main thread, during startup --
// before any renderer exists and therefore before anything a page can
// observe.  The result is cached here, and the IO thread only ever copies
// a string that is already in memory.

namespace {

// The cached payload.  Written once on the main thread during startup
// and only read afterwards, so a plain pointer swap is enough.
std::string* const g_cached_js_payload = new std::string();

// The payload as it was read, before any encoding.
std::string* const g_cached_js_payload_raw = new std::string();

// Reads the payload from the path on the command line.  Blocking; only
// the main thread may call this.
//
// The payload is the profile's values, without the code that installs
// them.  The code is compiled into the browser, so only this part
// travels.
void PrimeBrowserHandoverCache(const base::CommandLine& command_line) {
  const std::string payload_path =
      command_line.GetSwitchValueASCII("nova-js-payload");
  if (!payload_path.empty()) {
    base::FilePath file_path(base::FilePath::FromUTF8Unsafe(payload_path));
    std::string contents;
    if (base::ReadFileToString(file_path, &contents)) {
      *g_cached_js_payload_raw = contents;

      // The payload also goes on the command line of the renderer, and
      // a Windows command line cannot describe a document that contains
      // quotes, spaces and newlines.  Base64 can: it has none of them
      // and survives the trip intact.
      //
      // The early-startup variant is used because this runs before the
      // feature list is registered: the ordinary encoder consults a
      // feature flag, and asking for a flag that does not exist yet is
      // a fatal error.
      //
      // The payload is deflated first, for the same reason the profile
      // is: the renderer is started with both of them on its command
      // line, and a Windows command line holds at most 32,767
      // characters.  The two together do not fit uncompressed.
      const std::string deflated = DeflateForCommandLine(contents);
      *g_cached_js_payload = base::Base64EncodeEarlyStartup(
          base::as_byte_span(std::string_view(
              deflated.empty() ? contents : deflated)));
    } else {
      LOG(ERROR) << "NOVA: the JavaScript payload could not be read: "
                 << payload_path << " ("
                 << logging::SystemErrorCodeToString(
                        logging::GetLastSystemErrorCode())
                 << ")";
    }
  }
}

}  // namespace

// Called on the main thread during startup, before the first renderer is
// spawned.  From here on the IO thread never touches the disk.
void PrimeBrowserHandover(const base::CommandLine& command_line) {
  PrimeBrowserHandoverCache(command_line);
}

std::string ReadJsRuntimeForBrowser() {
  return *g_cached_js_payload;
}

// The payload itself, for the Mojo channel.
//
// The Mojo channel carries the payload as it stands: it is not limited
// to the length of a command line, so the payload does not have to be
// encoded, and the renderer does not have to decode it.
//
// The code is not sent: it is compiled into the browser.
std::string ReadJsRuntimeForRenderer() {
  return *g_cached_js_payload_raw;
}

}  // namespace nova
