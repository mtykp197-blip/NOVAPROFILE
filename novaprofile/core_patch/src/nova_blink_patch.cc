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

#include "nova_chromium_hooks.h"
#include "nova_core_config.h"
#include "nova_pixel_noise.h"

#include <algorithm>
#include <cmath>
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
// The trace.
//
// The trace is a line on Chromium's own log.  That is not the obvious
// place to put it: the log is noisy, and a trace that is switched on
// and off is easier to live with.
//
// The obvious alternatives do not work.  An environment variable does
// not reach the renderer, which is a child process, and Chromium does
// not necessarily hand the environment to it.  A file does not work
// either: the file stays empty.
//
// The log does work, because the standard error of a child process is
// the standard error of the browser.  A line logged in the renderer
// comes out of the browser's own standard error, next to everything
// else.
inline bool NovaTraceOn() {
  return true;
}


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
    // The unit values are derived once, here, so that every surface
    // agrees on them.  A surface that derived its own would draw a
    // different value from the same seed and the profile would report
    // two different core counts.
    unit_values_ = DeriveUnitValues(
        config, config.navigator.hardware_concurrency_options,
        config.navigator.device_memory_options,
        config.navigator.max_touch_points_options, config.screen.width,
        config.screen.height, config.screen.device_pixel_ratio,
        config.screen.device_pixel_ratio_options);
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
    // The lazy load is the *only* load for a child process: the
    // document arrives on the command line and there is nothing to
    // read from the disk.  For the browser process the document was
    // handed over by the startup code, before any child existed.
    //
    // A path is never read here.  The lazy load may run on a thread
    // that is not allowed to wait on the disk -- a read there trips the
    // "tls_blocking_disallowed" DCHECK and takes the whole browser down
    // -- and the child processes are sandboxed and cannot read the file
    // anyway.
    const std::string inline_source =
        command_line->GetSwitchValueASCII(kConfigSourceSwitchName);
    const std::string path = command_line->GetSwitchValueASCII("nova-config");
    if (inline_source.empty() && path.empty()) {
      return;
    }

    // The browser process reads its copy of the profile from the
    // hand-over.  The children are given the document itself.
    std::string document;
    if (!inline_source.empty()) {
      if (!InflateFromCommandLine(inline_source, &document)) {
        LOG(ERROR) << "NOVA: the profile configuration on the command line "
                      "is not valid base64";
        return;
      }
    } else {
      document = ReadBrowserConfigDocument();
      if (document.empty()) {
        return;
      }
    }

    ProfileConfig config;
    std::string error;
    if (!ParseProfileConfig(document, &config, &error)) {
      LOG(ERROR) << "NOVA: the profile configuration could not be parsed: "
                 << error;
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

  // The derived unit values.
  const UnitValues& UnitValueCache() {
    base::AutoLock guard(lock_);
    return unit_values_;
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
  UnitValues unit_values_;
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
  LOG(INFO) << "NOVA: init-core: type="
            << command_line.GetSwitchValueASCII("type").c_str()
            << " path=" << (int)path.size()
            << " inline=" << (int)inline_source.size();

  // The document itself is what a child process is given.  The browser
  // process reads the file itself, through the hand-over cache, and
  // that read happens on the main thread during startup -- this function
  // may run on a thread that is not allowed to wait on the disk, and a
  // read there trips the "tls_blocking_disallowed" DCHECK.
  //
  // The browser is given the path and the children are given the
  // document, so the two are told apart by the path: only the browser
  // has one.
  if (inline_source.empty()) {
    if (path.empty()) {
      return false;
    }
    // The browser process.  Its copy of the document was handed to it
    // by the startup code, which read the file and put the document
    // here.  The document is the raw JSON; the startup code has already
    // decoded it.
    const std::string document = ReadBrowserConfigDocument();
    if (document.empty()) {
      // Nothing was handed over: the browser was not started with a
      // profile it could read, or it was started without the hand-over
      // being primed.  Either way there is nothing to install.
      return false;
    }
    ProfileConfig config;
    std::string error;
    if (!ParseProfileConfig(document, &config, &error)) {
      LOG(ERROR) << "NOVA: the profile configuration could not be parsed: "
                 << error;
      return false;
    }
    NovaConfigHolder::Get().Initialize(config);
    LOG(INFO) << "NOVA: profile " << config.profile_id << " loaded (preset "
              << config.seed_config.preset_id << ")";
    return true;
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

// The derived unit values.
//
// The values in the configuration are what the profile asks for; these
// are what the core reports, after deriving the ones the profile left
// out.  A surface reads these, so that the whole browser agrees on one
// set of numbers.
const UnitValues& GetUnitValues() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  holder.EnsureInitialized();
  return holder.UnitValueCache();
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

  // The geometry is perturbed in the compositor, so that the rectangle
  // a page measures is the perturbed one and the layout behind it
  // agrees with it.
  if (config.rects.mode != "native_passthrough" &&
      config.rects.mode != "disabled") {
    surfaces.push_back("rects");
  }

  // The device inventory is the profile's own, so that the count of the
  // cameras and the microphones is the profile's and not the machine's.
  if (config.media_devices.mode != "native_passthrough" &&
      config.media_devices.mode != "disabled") {
    surfaces.push_back("media_devices");
  }

  // The readings of the motion sensors are the profile's own.
  //
  // The surface is *not* claimed here.  The native core has the
  // readings -- the accelerometer, the gyroscope and the gravity are in
  // the configuration -- but it does not deliver them: a
  // ``devicemotion`` event is delivered by the event machinery, and the
  // native core does not drive it.
  //
  // Claiming a surface the native core does not implement is the one
  // mistake this list must never make.  The JavaScript layer stands
  // down on the surfaces that are named here, so a name without an
  // implementation leaves the surface reporting the host's values --
  // and the host has no sensors at all, so the page waits for an
  // event that never comes.  That is a bigger difference from a phone
  // than any reading would have been.
  //
  // So the sensors stay with the JavaScript layer, which delivers the
  // event with the profile's readings and the profile's interval.

  // The readback of the canvas is perturbed in the rasterizer.
  if (config.canvas_2d.mode != "native_passthrough" &&
      config.canvas_2d.mode != "block") {
    surfaces.push_back("canvas");
  }

  // The audio parameters are the profile's own.
  //
  // The audio surface is not claimed either, for the same reason as the
  // sensors: the native core has no implementation that reaches the
  // audio stack.  The JavaScript layer builds the contexts with the
  // profile's rate and channel count, and that is what a site reads.

  // The storage estimate is served from the quota manager, so that the
  // quota and the usage are the profile's.
  if (config.storage_network.quota_bytes > 0) {
    surfaces.push_back("storage");
  }

  return surfaces;
}

// The declaration the JavaScript layer reads to learn which surfaces the
// native core owns.
//
// It is a local variable in the script that runs the layer.  The layer is
// handed the value as an argument, so the list never reaches the page: a
// script that enumerates the window's own properties finds nothing, and
// a script that reads the layer's source finds an argument rather than a
// global.
//
// The declaration used to be an assignment to a property on the window,
// and it was written only when the browser was started with the expose
// switch.  That was wrong twice over.
//
// The first wrongness was the switch.  The switch is a debugging aid,
// and the layer was made to depend on it: with the switch off -- which
// is how the browser runs in production -- the layer assumed that the
// native core owned nothing, and it patched every surface itself.  On
// the surfaces the native core already owns that produced two
// perturbations of the same value: the native one, and then the
// JavaScript one on top of it.  A page that measures the geometry sees
// the sum of the two, and the sum is not the number the profile
// describes.
//
// The second wrongness was the property.  A window property that does
// not exist otherwise is a marker, and a marker that says "this browser
// carries a fingerprint" is exactly what the browser must not carry.
std::string BuildNativeSurfaceDeclaration() {
  // The list is empty when the native core owns nothing -- which is the
  // case for a browser that was not patched at all, and for a profile
  // that asked for no perturbation at all.  The layer then patches
  // every surface itself, which is what an unpatched browser needs.
  const std::vector<std::string> surfaces = GetNativeOwnedSurfaces();
  std::string json = "[";
  for (size_t i = 0; i < surfaces.size(); ++i) {
    if (i)
      json += ",";
    json += "\"" + surfaces[i] + "\"";
  }
  json += "]";

  // The wrapper opens a scope of its own, binds the list inside it, and
  // the layer -- which is appended after this -- reads the binding and
  // closes the wrapper.  Nothing outside the wrapper can see the list,
  // and the window never gains a property for it.
  return "(function () {\n"
         "var __NOVA_NATIVE_SURFACES__ = " +
         json +
         ";\n";
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

// The version strings.
//
// The version of the context and the version of the shading language
// are read together with the unmasked renderer, and a script that
// finds three strings that do not belong together knows the GPU is a
// mask.  So they come from the profile as well.
//
// The strings are the whole ones -- "WebGL 2.0 (OpenGL ES 3.0
// Chromium)" -- and not the inner part, because the wrapper is what
// tells the script which API version the context implements.
std::string GetWebGLVersionString() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().webgl.version;
}

std::string GetWebGLShadingLanguageVersionString() {
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return std::string();
  }
  return holder.GetConfig().webgl.shading_language_version;
}

// ---------------------------------------------------------------------------
// The WebGL parameter table
//
// The parameter table is what identifies the GPU.  The unmasked renderer
// says "Mali-G78" and the table says how many texture units it has, how
// many uniform vectors a fragment shader may declare, how large a texture
// may be -- and a table that does not belong to the named renderer is the
// single easiest way to catch a spoofed GPU.
//
// So the whole table comes from the profile.  The values are read here
// rather than in the WebGL module, so that the WebGL module only has to
// ask for "the value of this parameter" and does not have to know what
// the profile says.
// ---------------------------------------------------------------------------

const WebGLParameters& GetWebGLParameters() {
  static const WebGLParameters kEmpty;
  NovaConfigHolder& holder = NovaConfigHolder::Get();
  if (!holder.IsInitialized()) {
    return kEmpty;
  }
  return holder.GetConfig().webgl.parameters;
}

// The profile's own value of a WebGL parameter, by its name.
//
// The names are the ones the control plane uses, which are the lower
// case form of the GL enumerant: ``max_texture_size`` for
// ``GL_MAX_TEXTURE_SIZE``.  A parameter the profile does not describe
// returns false, and the caller keeps the host's value.
//
// The value is returned through |out| and the function returns true when
// the profile describes the parameter.
bool GetWebGLParameter(const std::string& name, int* out) {
  if (!out) {
    return false;
  }
  const WebGLParameters& parameters = GetWebGLParameters();
  if (name == "max_texture_size") {
    *out = parameters.max_texture_size;
  } else if (name == "max_cube_map_texture_size") {
    *out = parameters.max_cube_map_texture_size;
  } else if (name == "max_3d_texture_size") {
    *out = parameters.max_3d_texture_size;
  } else if (name == "max_array_texture_layers") {
    *out = parameters.max_array_texture_layers;
  } else if (name == "max_renderbuffer_size") {
    *out = parameters.max_renderbuffer_size;
  } else if (name == "max_vertex_attribs") {
    *out = parameters.max_vertex_attribs;
  } else if (name == "max_vertex_uniform_vectors") {
    *out = parameters.max_vertex_uniform_vectors;
  } else if (name == "max_vertex_uniform_components") {
    *out = parameters.max_vertex_uniform_components;
  } else if (name == "max_fragment_uniform_vectors") {
    *out = parameters.max_fragment_uniform_vectors;
  } else if (name == "max_fragment_uniform_components") {
    *out = parameters.max_fragment_uniform_components;
  } else if (name == "max_varying_vectors") {
    *out = parameters.max_varying_vectors;
  } else if (name == "max_combined_texture_image_units") {
    *out = parameters.max_combined_texture_image_units;
  } else if (name == "max_vertex_texture_image_units") {
    *out = parameters.max_vertex_texture_image_units;
  } else if (name == "max_texture_image_units") {
    *out = parameters.max_texture_image_units;
  } else if (name == "max_color_attachments") {
    *out = parameters.max_color_attachments;
  } else if (name == "max_draw_buffers") {
    *out = parameters.max_draw_buffers;
  } else if (name == "max_samples") {
    *out = parameters.max_samples;
  } else if (name == "max_uniform_buffer_bindings") {
    *out = parameters.max_uniform_buffer_bindings;
  } else if (name == "sample_buffers") {
    *out = parameters.sample_buffers;
  } else if (name == "samples") {
    *out = parameters.samples;
  } else if (name == "max_texture_max_anisotropy_ext") {
    *out = parameters.max_texture_max_anisotropy_ext;
  } else {
    return false;
  }
  // A parameter the profile leaves at zero is one it does not describe.
  // The host's value is kept in that case.
  return *out != 0;
}

// The profile's own value of a WebGL parameter that is a range.
//
// The ranges are the aliased line width range and the aliased point size
// range.  They are read into |out| and the function returns true when
// the profile describes them.
bool GetWebGLParameterRange(const std::string& name, std::vector<double>* out) {
  if (!out) {
    return false;
  }
  const WebGLParameters& parameters = GetWebGLParameters();
  if (name == "aliased_line_width_range") {
    *out = parameters.aliased_line_width_range;
  } else if (name == "aliased_point_size_range") {
    *out = parameters.aliased_point_size_range;
  } else {
    return false;
  }
  return !out->empty();
}

// The profile's own value of the viewport dimensions, which is a pair.
bool GetWebGLViewportDims(std::vector<int>* out) {
  if (!out) {
    return false;
  }
  const WebGLParameters& parameters = GetWebGLParameters();
  if (parameters.max_viewport_dims.size() != 2) {
    return false;
  }
  *out = parameters.max_viewport_dims;
  return true;
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

// The profile's own device inventory.
//
// The host's inventory is the machine's, not the profile's.  A desktop
// has a camera that an Android phone does not, and a machine with two
// microphones has two where the profile has one.  A page that counts
// the devices -- or reads their labels -- learns about the machine, and
// a machine is shared by every profile that runs on it.  So the
// enumeration is replaced by the profile's own list.
//
// The identifiers in that list were derived from the profile's salt when
// the profile was generated, so they are already unique to the profile
// and they do not have to be rewritten again here.  Rewriting them a
// second time would be harmless for the uniqueness and would change
// the value the profile declares, which is what the verifier compares
// against.
std::vector<std::string> NovaMediaDeviceKinds() {
  std::vector<std::string> kinds;
  if (!IsNovaCoreInitialized()) {
    return kinds;
  }
  const ProfileConfig& config = GetNovaConfigRef();
  if (config.media_devices.mode == "native_passthrough" ||
      config.media_devices.mode == "disabled") {
    return kinds;
  }
  if (!config.media_devices.audio_inputs.empty()) {
    kinds.push_back("audioinput");
  }
  if (!config.media_devices.audio_outputs.empty()) {
    kinds.push_back("audiooutput");
  }
  if (!config.media_devices.video_inputs.empty()) {
    kinds.push_back("videoinput");
  }
  return kinds;
}

std::vector<NovaMediaDevice> NovaMediaDevices(const std::string& kind) {
  std::vector<NovaMediaDevice> devices;
  if (!IsNovaCoreInitialized()) {
    return devices;
  }
  const ProfileConfig& config = GetNovaConfigRef();
  if (config.media_devices.mode == "native_passthrough" ||
      config.media_devices.mode == "disabled") {
    return devices;
  }

  if (kind == "audioinput") {
    for (const auto& entry : config.media_devices.audio_inputs) {
      NovaMediaDevice device;
      device.device_id = entry.device_id;
      device.group_id = entry.group_id;
      device.label = entry.label;
      device.kind = "audioinput";
      devices.push_back(std::move(device));
    }
  } else if (kind == "audiooutput") {
    for (const auto& entry : config.media_devices.audio_outputs) {
      NovaMediaDevice device;
      device.device_id = entry.device_id;
      device.group_id = entry.group_id;
      device.label = entry.label;
      device.kind = "audiooutput";
      devices.push_back(std::move(device));
    }
  } else if (kind == "videoinput") {
    for (const auto& entry : config.media_devices.video_inputs) {
      NovaMediaDevice device;
      device.device_id = entry.device_id;
      device.group_id = entry.group_id;
      device.label = entry.label;
      device.kind = "videoinput";
      devices.push_back(std::move(device));
    }
  }
  return devices;
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

// Whether the family is one of the CSS generic families.
//
// A generic family is not a file: it is a request for "whatever the
// platform offers for this kind of text".  It is therefore always
// available, and it is never part of the profile's inventory -- a
// page that enumerates the fonts of a device does not find "serif"
// among them.
bool IsGenericFontFamily(const std::string& family_name) {
  static const char* kGenericFamilies[] = {
      "serif",        "sans-serif",   "monospace",   "cursive",
      "fantasy",      "system-ui",    "ui-serif",    "ui-sans-serif",
      "ui-monospace", "ui-rounded",   "math",        "emoji",
      "fangsong",     "-webkit-body",
  };
  for (const char* generic : kGenericFamilies) {
    if (base::EqualsCaseInsensitiveASCII(family_name, generic)) {
      return true;
    }
  }
  return false;
}

// The size adjustment the profile's own font inventory applies.
//
// Why the inventory needs a metric of its own
// --------------------------------------------
//
// The standard font probe asks a question that has two halves.  The
// first is "is this family on the device?", and the second is "does it
// measure differently from the generic font?".  A device answers the
// second half by accident: a family that is present has its own
// metrics, and a family that is absent measures exactly as the generic.
//
// So the answer the probe reads is *the difference between the family's
// metrics and the generic's*.  On the machine the browser runs on, the
// host's fonts answer it -- and they answer it with the host's
// inventory, which is the inventory of a Windows workstation.
//
// The profile declares its own inventory: the fonts of an Android
// phone.  On a Windows machine none of them is installed, so without
// help they would all measure as the generic and the probe would
// report an empty inventory.
//
// The help is a size adjustment: the family is resolved as usual, and
// then its size is scaled by a factor that comes from the profile's
// salt and the family's name.  The scale is small -- a fraction of a
// percent -- and it is what makes the family measure differently from
// the generic, which is exactly the answer the probe is looking for.
//
// Two properties matter and both come from the salt:
//
//   * the factor is the same for every run of the profile, so the
//     inventory is stable: a device whose fonts move between two
//     visits is a device that can be singled out;
//   * the factor is different for every family and every profile, so
//     two profiles never share a fingerprint.
//
// The factor is derived from the salt with a hash, so it does not
// depend on the order of the inventory, on the machine, or on the
// font that happens to be installed.
double NovaFontSizeAdjustment(const std::string& family_name,
                               const std::string& salt) {
  if (salt.empty()) {
    return 1.0;
  }

  // A small FNV-1a over the salt and the family name.  It is the same
  // hash the JavaScript layer uses, so the two layers agree on the
  // value when both of them are active.
  uint64_t hash = 1469598103934665603ULL;
  auto mix = [&hash](const std::string& text) {
    for (char character : text) {
      hash ^= static_cast<unsigned char>(character);
      hash *= 1099511628211ULL;
    }
    hash ^= 0x5c;
    hash *= 1099511628211ULL;
  };
  mix(salt);
  mix(family_name);

  // The factor is in [0.965, 1.035): the family's width differs from
  // the generic's by a couple of percent, which is far above the
  // two-decimal rounding the probe compares at, and far below the
  // difference between two real fonts.
  //
  // The factor is never exactly one.  A factor of one is the *absence*
  // of an adjustment: the family would measure exactly as the generic
  // does and the probe would report it as absent, which is the one
  // outcome the adjustment exists to prevent.
  //
  // The range is wider than it looks like it needs to be, because the
  // platform rounds the size of a font to whole pixels before it is
  // rasterized.  On Windows the height of a face is a whole number of
  // pixels, so a factor of 0.997 at 72 pixels lands on 72 pixels again
  // and the family measures exactly as the generic does.  The factor
  // therefore has to be big enough to move the size by at least one
  // pixel; the caller checks that and asks for another one when it is
  // not.
  const double unit = static_cast<double>(hash % 7000) / 100000.0;
  double adjustment = 0.965 + unit * 0.07;
  if (adjustment == 1.0) {
    adjustment = 1.001;
  }
  return adjustment;
}

// The size adjustment the profile's font inventory applies to a family,
// with the size the family is asked for taken into account.
//
// The platform rounds a font's size to whole pixels, so an adjustment
// has to move the size by at least one pixel to have any effect at
// all.  This function returns the adjustment and, through |applied|,
// the size the family is actually asked for.  When the adjustment of
// the family does not move the size, another one is drawn: the family
// has to measure differently from the generic, which is the one thing
// the adjustment exists for.
double NovaFontSizeAdjustmentForSize(const std::string& family_name,
                                     const std::string& salt,
                                     float font_size,
                                     float* applied) {
  double adjustment = NovaFontSizeAdjustment(family_name, salt);
  if (salt.empty() || font_size <= 0.0f) {
    if (applied) {
      *applied = font_size;
    }
    return adjustment;
  }

  // The size the platform will use: it rounds to whole pixels, so the
  // comparison is made on the rounded value.
  auto rounded = [](double size) {
    return static_cast<double>(static_cast<long>(size + 0.5));
  };

  const double base = rounded(font_size);
  double best = adjustment;
  double best_delta = std::abs(rounded(base * adjustment) - base);

  // When the factor that was drawn does not move the size, another one
  // is drawn from the same hash, so the answer stays a function of the
  // salt and the family alone.
  if (best_delta < 1.0) {
    uint64_t hash = 1469598103934665603ULL;
    auto mix = [&hash](const std::string& text) {
      for (char character : text) {
        hash ^= static_cast<unsigned char>(character);
        hash *= 1099511628211ULL;
      }
      hash ^= 0x5c;
      hash *= 1099511628211ULL;
    };
    mix(salt);
    mix(family_name);
    for (int attempt = 0; attempt < 64 && best_delta < 1.0; ++attempt) {
      hash ^= 0x9e;
      hash *= 1099511628211ULL;
      const double candidate =
          0.965 + static_cast<double>(hash % 7000) / 100000.0 * 0.07;
      if (candidate == 1.0) {
        continue;
      }
      const double delta = std::abs(rounded(base * candidate) - base);
      if (delta > best_delta) {
        best_delta = delta;
        best = candidate;
      }
    }
    // Nothing moved the size, so the size is moved by hand: the family
    // is asked for one pixel less.
    if (best_delta < 1.0) {
      best = (base - 1.0) / base;
    }
  }

  if (applied) {
    *applied = static_cast<float>(base * best);
  }
  return best;
}

// ---------------------------------------------------------------------------
// The geometry perturbation
//
// The perturbation is applied only when the profile asked for it.  A
// profile in the "noise" mode gets the deltas of its own seed; a profile
// in the "disabled" mode gets the rectangle the layout produced, so the
// page sees the host geometry untouched.
//
// The deltas are whole pixels and they are the same for every surface.
// That is a deliberate choice, and it is the same choice the JavaScript
// layer and the simulator make, because the three of them have to agree
// exactly: a page that measures the geometry has to get the number the
// profile describes, and not the sum of two different perturbations.
//
// A fractional offset was tried and it is wrong for two reasons.  The
// first is that a half-pixel rectangle is a tell: a real layout produces
// rectangles that are aligned to the pixel grid, and one that lands
// between two pixels is a rectangle that no layout engine produced.
// The second is that the element rectangle and the range rectangle move
// by different amounts, and a page that compares the two -- a classic
// cross-surface consistency check -- finds them disagreeing about where
// the text is.  The text does not move relative to its box, so the two
// must move together.
//
// The layout itself is not perturbed: only the rectangle the page reads
// is.  A page that reads the geometry and a page that hits the geometry
// therefore see two different things, and that is the price of a
// perturbation that does not rebuild the layout.
void ApplyRectDeltas(gfx::RectF& rect, const char* surface) {
  if (!IsNovaCoreInitialized()) {
    return;
  }
  const ProfileConfig& config = GetNovaConfigRef();
  if (config.rects.mode == "disabled" ||
      config.rects.mode == "native_passthrough") {
    return;
  }
  const int delta_x = GetRectDeltaX();
  const int delta_y = GetRectDeltaY();
  const int delta_width = GetRectDeltaWidth();
  const int delta_height = GetRectDeltaHeight();
  if (delta_x == 0 && delta_y == 0 && delta_width == 0 && delta_height == 0) {
    return;
  }

  rect.Offset(delta_x, delta_y);
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
// The profile's payload, as the JSON the layer reads.
//
// It arrives either on the command line or over the Mojo channel, and
// the Mojo one wins: it is the same payload, but it did not pass
// through the process table.
//
// The result is *not* cached.
//
// The payload has two transports, and the second one arrives late: the
// renderer connects to the browser over the Mojo channel, and the
// channel delivers the payload when it is ready.  A navigation can
// begin before that happens -- and the first navigation is the one
// that matters, because it is the one the page sees the values on.
//
// A cached value would freeze whatever was there at the first read.
// When the first read happens before the channel has delivered
// anything, the cached value is the empty one, and it stays empty for
// the life of the renderer: the layer never runs, and the page sees
// the host's values for every axis the layer owns.
//
// So the payload is read every time it is asked for.  The command line
// is read once and kept; the channel is polled.
const std::string& JsRuntimePayload() {
  static thread_local std::string payload;

  // The payload that arrived over the Mojo channel wins over the one
  // on the command line.
  if (!g_js_payload_from_mojo->empty()) {
    payload = *g_js_payload_from_mojo;
    return payload;
  }

  const base::CommandLine* command_line =
      base::CommandLine::ForCurrentProcess();
  if (!command_line) {
    payload.clear();
    return payload;
  }

  // The switch carries the payload, base64 encoded.  A Windows
  // command line cannot describe a document that contains quotes,
  // spaces and newlines, so the browser encodes it before it hands
  // it over; base64 has none of them and survives the trip intact.
  const std::string inline_encoded =
      command_line->GetSwitchValueASCII(kJsPayloadSwitchName);
  if (inline_encoded.empty()) {
    payload.clear();
    return payload;
  }

  std::string decoded;
  if (!InflateFromCommandLine(inline_encoded, &decoded)) {
    LOG(ERROR) << "NOVA: the JavaScript payload on the command line is "
                  "not valid base64";
    payload.clear();
    return payload;
  }
  payload = std::move(decoded);
  return payload;
}

}  // namespace

// Called by the renderer when the browser hands the payload over the
// Mojo channel.
//
// The payload that arrives here is the whole payload, so it wins over
// anything the command line carried.
void SetJsRuntimeFromMojo(std::string runtime) {
  LOG(INFO) << "NOVA: mojo: runtime len=" << (int)runtime.size();
  *g_js_payload_from_mojo = std::move(runtime);
}

const std::string& GetJsRuntimeSource() {
  static thread_local std::string source;
  const std::string& payload = JsRuntimePayload();
  LOG(INFO) << "NOVA: js-runtime: payload=" << (int)payload.size();
  if (payload.empty()) {
    source.clear();
    return source;
  }
  source = payload + "\n" + GetJsRuntimeCode();
  return source;
}

bool HasJsRuntime() {
  return !GetJsRuntimeSource().empty();
}

// The payload the browser hands the renderer.
//
// The payload is the profile's values, as a JSON document.  It arrives
// either as the raw JSON or wrapped in the assignment that installs it
// on the window; the wrapper is stripped here, because the value has to
// end up as an argument to the layer and never as a property of the
// window.
//
// A payload on the window would be a fingerprint of its own: it carries
// the profile's salts, the device identifiers and the font list, and a
// script that enumerates the window's own properties would get the
// whole thing.
std::string ExtractPayloadJson(const std::string& payload) {
  const std::string trimmed(
      base::TrimWhitespaceASCII(payload, base::TRIM_ALL));
  const std::string prefix = "window.__NOVA_PROFILE__=";
  if (!base::StartsWith(trimmed, prefix)) {
    // The payload is already the bare JSON.
    return trimmed;
  }
  std::string body = trimmed.substr(prefix.size());
  // The assignment ends with a semicolon.  The JSON does not contain
  // one at the top level, so the last one is the statement's.
  const size_t last = body.rfind(';');
  if (last != std::string::npos) {
    body = body.substr(0, last);
  }
  return std::string(base::TrimWhitespaceASCII(body, base::TRIM_ALL));
}

// The script the renderer actually runs.
//
// It is the JavaScript layer, wrapped in a scope that holds the two
// values the layer reads: the native core's list of the surfaces it
// owns, and the profile's own payload.
//
// The wrapper exists for two reasons.
//
// The first is the handoff.  Without the list the layer assumes that the
// native core owns nothing, and it patches every surface itself.  On
// the surfaces the native core already owns that produced two
// perturbations of the same value: the native one, and then the
// JavaScript one on top of it.  A page that measures the geometry sees
// the sum of the two, and the sum is not the number the profile
// describes.
//
// The second is the marker.  Both the list and the payload used to be
// published as properties on the window, and a property that does not
// exist otherwise is a fingerprint of its own: a script that enumerates
// the window's own properties finds the list with a single enumeration,
// and the payload -- which carries the profile's salts, the device
// identifiers and the font list -- is the whole profile in the clear.
// A browser that carries a profile's fingerprint must not carry a name
// that says it does.  Inside a wrapper both are local variables, and
// they are gone by the time the page's first script runs.
//
// The result is *not* cached.  The script arrives over a Mojo channel,
// and the first navigation can start before the channel has delivered
// it; a value that was computed once and kept would then be the empty
// one, for the rest of the renderer's life.  Recomputing costs a string
// concatenation, and this runs once per document.
const std::string& GetEffectiveJsRuntimeSource() {
  static thread_local std::string source;
  const std::string& payload = JsRuntimePayload();
  LOG(INFO) << "NOVA: effective: runtime len=" << (int)payload.size();
  if (payload.empty()) {
    source.clear();
    return source;
  }

  // The declaration names only the surfaces the native core owns.
  // When it owns none of them -- which is the case for a browser that
  // was not patched at all -- the layer is left to do everything.
  const std::string declaration = BuildNativeSurfaceDeclaration();
  source = declaration + "var __NOVA_PAYLOAD__ = " +
           ExtractPayloadJson(payload) + ";\n" + GetJsRuntimeCode() +
           "\n})();\n";
  return source;
}

const char kJsPayloadSwitchName[] = "nova-js-payload-source";

}  // namespace nova
