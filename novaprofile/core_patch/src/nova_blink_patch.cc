// NOVA Profile Core -- Blink integration.
//
// This file wires the configuration into Blink.  It is deliberately placed
// *inside* Blink rather than in a JavaScript shim, because anything that
// runs in the JS world is observable:
//
//   - Function.prototype.toString() returns the shim's source
//   - Object.getOwnPropertyDescriptor() reveals an accessor
//   - A Proxy traps every property access
//   - navigator.webdriver is set to true
//
// By overriding the native C++ accessors instead, the page sees ordinary
// native properties: the same shape, the same enumerability, the same
// `toString()` output as a stock Android Chrome.
//
// The patch touches five places in Blink:
//
//   1. NavigatorID            -- userAgent, platform, hardwareConcurrency, ...
//   2. NavigatorUAData        -- the client hints: brands, platform version,
//                                architecture, bitness and model
//   3. Screen                -- screen metrics and the device pixel ratio
//   4. WebGLRenderingContext -- the unmasked renderer and readPixels
//   5. CanvasRenderingContext2D -- the pixel readback paths
//
// Every one of them reads from a single process-wide NovaConfig, which is
// why the main frame, a cross-origin iframe, a sandboxed iframe, a Web
// Worker, a SharedWorker and a ServiceWorker all report identical values:
// they are separate JS worlds but the same native process.

#include "nova_core_config.h"
#include "nova_pixel_noise.h"

#include <memory>
#include <string>
#include <vector>

#include "base/check.h"
#include "base/command_line.h"
#include "base/files/file_path.h"
#include "base/logging.h"
#include "base/memory/ref_counted.h"
#include "base/synchronization/lazy_init.h"
#include "base/synchronization/lock.h"
#include "cc/paint/sk_bitmap.h"
#include "content/public/browser/browser_process.h"
#include "gpu/config/gpu_switches.h"
#include "gpu/config/webgl_context.h"
#include "third_party/blink/public/platform/webgl/webgl_rendering_context.h"
#include "third_party/blink/renderer/core/canvas/canvas_rendering_context_2d.h"
#include "third_party/blink/renderer/core/html/navigator_id.h"
#include "third_party/blink/renderer/core/html/navigator_ua_data.h"
#include "third_party/blink/renderer/core/screen/screen.h"
#include "third_party/blink/renderer/platform/bindings/v8_binding.h"
#include "third_party/blink/renderer/platform/webgl/webgl_context_provider.h"

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
    const base::CommandLine& command_line = base::CommandLine::ForCurrentProcess();
    const std::string path = command_line.GetSwitchValueASCII("nova-config");
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

  ProfileConfig Get() {
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
  const ProfileConfig& GetRef() {
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

// ---------------------------------------------------------------------------
// NavigatorID
// ---------------------------------------------------------------------------

String NavigatorID::userAgent() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardUserAgent();
  }
  return String::FromUTF8(holder.Get().navigator.user_agent);
}

String NavigatorID::appVersion() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardAppVersion();
  }
  return String::FromUTF8(holder.Get().navigator.app_version);
}

String NavigatorID::appName() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardAppName();
  }
  return String::FromUTF8(holder.Get().navigator.app_name);
}

String NavigatorID::appCodeName() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardAppCodeName();
  }
  return String::FromUTF8(holder.Get().navigator.app_code_name);
}

String NavigatorID::vendor() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardVendor();
  }
  return String::FromUTF8(holder.Get().navigator.vendor);
}

String NavigatorID::vendorSub() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardVendorSub();
  }
  return String::FromUTF8(holder.Get().navigator.vendor_sub);
}

String NavigatorID::product() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardProduct();
  }
  return String::FromUTF8(holder.Get().navigator.product);
}

String NavigatorID::productSub() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardProductSub();
  }
  return String::FromUTF8(holder.Get().navigator.product_sub);
}

String NavigatorID::platform() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardPlatform();
  }
  return String::FromUTF8(holder.Get().navigator.platform);
}

unsigned NavigatorID::hardwareConcurrency() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardHardwareConcurrency();
  }
  return static_cast<unsigned>(holder.Get().navigator.hardware_concurrency);
}

double NavigatorID::deviceMemory() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardDeviceMemory();
  }
  return static_cast<double>(holder.Get().navigator.device_memory);
}

int NavigatorID::maxTouchPoints() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardMaxTouchPoints();
  }
  return holder.Get().navigator.max_touch_points;
}

String NavigatorID::language() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardLanguage();
  }
  return String::FromUTF8(holder.Get().navigator.language);
}

Vector<String> NavigatorID::languages() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorID::StandardLanguages();
  }
  Vector<String> result;
  for (const std::string& language : holder.Get().navigator.languages) {
    result.push_back(String::FromUTF8(language));
  }
  return result;
}

bool NavigatorID::webdriver() const {
  // Always false.  A profile that reports true is trivially identified.
  return false;
}

// ---------------------------------------------------------------------------
// NavigatorUAData -- the client hints
//
// The client hints are the axis an anti-bot vendor reads first: the brand
// list gives the browser build away, and the high-entropy values give the
// device away.  A profile that patches the User-Agent but not these
// contradicts itself in the very first request a page makes.
//
// The accessors are patched at the Blink level for the same reason the
// User-Agent is: a JavaScript shim would be observable through
// Object.getOwnPropertyDescriptor() and Function.prototype.toString().
// ---------------------------------------------------------------------------

Vector<NavigatorUABrandVersion> NavigatorUAData::brands() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultBrands();
  }
  const UserAgentDataConfig& uad = holder.GetRef().user_agent_data;
  Vector<NavigatorUABrandVersion> result;
  for (const BrandVersion& brand : uad.brands) {
    result.push_back(NavigatorUABrandVersion(
        String::FromUTF8(brand.brand), String::FromUTF8(brand.version)));
  }
  return result;
}

Vector<NavigatorUABrandVersion> NavigatorUAData::fullVersionList() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultFullVersionList();
  }
  const UserAgentDataConfig& uad = holder.GetRef().user_agent_data;
  Vector<NavigatorUABrandVersion> result;
  for (const BrandVersion& brand : uad.full_version_list) {
    result.push_back(NavigatorUABrandVersion(
        String::FromUTF8(brand.brand), String::FromUTF8(brand.version)));
  }
  return result;
}

bool NavigatorUAData::mobile() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultMobile();
  }
  return holder.GetRef().user_agent_data.mobile;
}

String NavigatorUAData::platform() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultPlatform();
  }
  return String::FromUTF8(holder.GetRef().user_agent_data.platform);
}

String NavigatorUAData::platformVersion() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultPlatformVersion();
  }
  return String::FromUTF8(holder.GetRef().user_agent_data.platform_version);
}

String NavigatorUAData::architecture() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultArchitecture();
  }
  return String::FromUTF8(holder.GetRef().user_agent_data.architecture);
}

String NavigatorUAData::bitness() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultBitness();
  }
  return String::FromUTF8(holder.GetRef().user_agent_data.bitness);
}

String NavigatorUAData::model() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultModel();
  }
  return String::FromUTF8(holder.GetRef().user_agent_data.model);
}

bool NavigatorUAData::wow64() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return NavigatorUAData::DefaultWOW64();
  }
  return holder.GetRef().user_agent_data.wow64;
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

int Screen::width() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardWidth();
  }
  return holder.Get().screen.width;
}

int Screen::height() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardHeight();
  }
  return holder.Get().screen.height;
}

int Screen::availWidth() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardAvailWidth();
  }
  return holder.Get().screen.avail_width;
}

int Screen::availHeight() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardAvailHeight();
  }
  return holder.Get().screen.avail_height;
}

int Screen::availLeft() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardAvailLeft();
  }
  return holder.Get().screen.avail_left;
}

int Screen::availTop() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardAvailTop();
  }
  return holder.Get().screen.avail_top;
}

int Screen::colorDepth() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardColorDepth();
  }
  return holder.Get().screen.color_depth;
}

int Screen::pixelDepth() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardPixelDepth();
  }
  return holder.Get().screen.pixel_depth;
}

double Screen::devicePixelRatio() const {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return Screen::StandardDevicePixelRatio();
  }
  return holder.Get().screen.device_pixel_ratio;
}

// ---------------------------------------------------------------------------
// WebGL -- the unmasked renderer and readPixels
// ---------------------------------------------------------------------------

// The unmasked renderer is served through WEBGL_debug_renderer_info.  It
// is patched at the WebGLContext level so that the value is identical in
// every context and cannot be distinguished from a real GPU by its
// lifetime or by the way it is exposed.
std::string UnmaskedVendorString() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.Get().webgl.unmasked_vendor;
}

std::string UnmaskedRendererString() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.Get().webgl.unmasked_renderer;
}

// The readback path.  This is the single most important hook in the
// graphics stack: a shader that renders interpolated coordinates and then
// reads them back is exactly the technique the anti-bot vendors use to
// detect a modified WebGL implementation.
//
// The noise is applied to the buffer that is handed back to the script,
// so the value the script observes is already perturbed -- there is no
// way for the script to recover the original pixels.
void MaybeNoiseWebGLReadback(WebGLRenderingContextBase* context,
                              const gfx::Size& size,
                              uint8_t* data,
                              int data_length) {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized() || !data || size.IsEmpty()) {
    return;
  }
  const ProfileConfig config = holder.Get();
  if (config.webgl.mode == "native_passthrough" ||
      config.webgl.mode == "disabled") {
    return;
  }
  if (!config.webgl.image_noise.enabled) {
    return;
  }

  const size_t bytes_per_row = static_cast<size_t>(size.width()) * 4;
  ApplyPixelNoise(data, bytes_per_row, size.width(), size.height(),
                  holder.WebGLKey(), config.canvas_2d.noise_strength);
}

// ---------------------------------------------------------------------------
// Canvas 2D
// ---------------------------------------------------------------------------

// The readback paths of the 2D canvas.  All of them funnel through the
// same SkBitmap readback, so one hook covers toDataURL, toBlob,
// getImageData and the OffscreenCanvas variants.
void MaybeNoiseCanvasBitmap(SkBitmap* bitmap, double strength) {
  if (!bitmap || bitmap->isEmpty()) {
    return;
  }
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return;
  }
  const ProfileConfig config = holder.Get();
  if (config.canvas_2d.mode == "native_passthrough" ||
      config.canvas_2d.mode == "block") {
    return;
  }

  bitmap->lockPixels();
  ApplyPixelNoise(bitmap->getAddr32(0, 0), bitmap->rowBytes(),
                  static_cast<int>(bitmap->width()),
                  static_cast<int>(bitmap->height()), holder.CanvasKey(),
                  strength);
  bitmap->unlockPixels();
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
  return holder.Get();
}

// The same configuration, without the copy.  Use this on the hot paths --
// the client hints are read on every request.
const ProfileConfig& GetNovaConfigRef() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  holder.EnsureInitialized();
  return holder.GetRef();
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
  return holder.Get().webgl.unmasked_vendor;
}

std::string GetUnmaskedRendererString() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.Get().webgl.unmasked_renderer;
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
  return static_cast<double>(holder.Get().audio.sample_rate);
}

double GetAudioBaseLatency() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().audio.base_latency;
}

double GetAudioOutputLatency() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().audio.output_latency;
}

int GetAudioChannelCount() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.Get().audio.channel_count;
}

int GetAudioMaxChannelCount() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.Get().audio.max_channel_count;
}

PixelNoiseKey GetAudioNoiseKey() {
  return NovaConfigHolder::Get().AudioKey();
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
  return holder.Get().rects.delta_x;
}

int GetRectDeltaY() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.Get().rects.delta_y;
}

int GetRectDeltaWidth() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.Get().rects.delta_width;
}

int GetRectDeltaHeight() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.Get().rects.delta_height;
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
  return holder.Get().media_devices.salt;
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
  return holder.Get().sensors.accelerometer.x;
}

double GetSensorAccelerometerY() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().sensors.accelerometer.y;
}

double GetSensorAccelerometerZ() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().sensors.accelerometer.z;
}

double GetSensorGyroscopeX() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().sensors.gyroscope.x;
}

double GetSensorGyroscopeY() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().sensors.gyroscope.y;
}

double GetSensorGyroscopeZ() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().sensors.gyroscope.z;
}

double GetSensorGravityZ() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0.0;
  }
  return holder.Get().sensors.gravity.z;
}

std::string GetSensorSalt() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.Get().sensors.salt;
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
  return holder.Get().storage_network.quota_bytes;
}

uint64_t GetStorageUsageBytes() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return 0;
  }
  return holder.Get().storage_network.usage_bytes;
}

std::string GetStorageSalt() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.Get().storage_network.salt;
}

// ---------------------------------------------------------------------------
// The WebGPU adapter
// ---------------------------------------------------------------------------

std::string GetWebGpuSalt() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.Get().webgpu.salt;
}

}  // namespace nova
