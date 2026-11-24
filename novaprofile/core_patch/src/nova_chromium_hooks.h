// NOVA Profile Core -- the Chromium integration patches.
//
// This header is included by the Chromium source files that need to serve
// the spoofed values.  It is deliberately tiny: a single inline function
// per patched accessor, so the diff against the Chromium tree stays
// reviewable.
//
//   third_party/blink/renderer/core/events/navigator_events.cc
//   third_party/blink/renderer/core/execution_context/navigator_base.cc
//   third_party/blink/renderer/core/frame/navigator.cc
//   third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc
//   third_party/blink/renderer/core/frame/navigator_device_memory.cc
//   third_party/blink/renderer/core/frame/navigator_id.cc
//   third_party/blink/renderer/core/frame/navigator_language.cc
//   third_party/blink/renderer/core/frame/navigator_ua.cc
//   third_party/blink/renderer/core/frame/navigator_ua_data.cc
//   third_party/blink/renderer/core/frame/screen.cc
//   third_party/blink/renderer/core/frame/local_dom_window.cc
//   third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc
//   third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc
//   content/public/browser/content_main_runner.cc
//
// The hooks layer itself lives in
//
//   third_party/blink/renderer/core/nova/nova_core_config.{h,cc}
//   third_party/blink/renderer/core/nova/nova_pixel_noise.{h,cc}
//   third_party/blink/renderer/core/nova/nova_blink_patch.cc
//
// and is built as the target
// ``//third_party/blink/renderer/core/nova:nova_core``.

#ifndef NOVA_PROFILE_CORE_CHROMIUM_HOOKS_H_
#define NOVA_PROFILE_CORE_CHROMIUM_HOOKS_H_

#include "nova_core_config.h"
#include "nova_pixel_noise.h"
#include "nova_unit_values.h"

#include <cstdint>
#include <string>
#include <vector>

#include "base/command_line.h"
#include "ui/gfx/geometry/rect_f.h"

namespace blink {
class LocalDOMWindow;
}  // namespace blink

namespace blink {
class Document;
}  // namespace blink

namespace blink {
class ExecutionContext;
}  // namespace blink

namespace nova {

// ---------------------------------------------------------------------------
// The configuration accessor.
//
// The config is held in the browser process and replicated into the
// renderer, the GPU process and the network service through a Mojo
// shared-memory region.  Every process therefore reports exactly the
// same values, which is what makes a cross-context check succeed.
// ---------------------------------------------------------------------------

bool IsNovaCoreInitialized();
ProfileConfig GetNovaConfig();

// The same configuration without the copy.  Use this on the hot paths:
// the client hints are read on every request.
const ProfileConfig& GetNovaConfigRef();

// The unit values -- the core count, the memory, the touch points, the
// panel -- as the core reports them.
//
// The values in the configuration are what the profile *asks* for.  The
// unit values are what the core *derives*, when the profile left a value
// out: the core count of a unit of a family whose SoC has eight cores
// is drawn from the family's own range, and two units of that family
// never draw the same one.
//
// A surface reads the unit values rather than the configuration, so that
// the derivation happens in exactly one place and every surface agrees.
const UnitValues& GetUnitValues();

// The noise keys, one per readback surface.
PixelNoiseKey GetCanvasNoiseKey();
PixelNoiseKey GetWebGLNoiseKey();
PixelNoiseKey GetOffscreenNoiseKey();

// The WebGL identity strings.
std::string GetUnmaskedVendorString();
std::string GetUnmaskedRendererString();

// The version strings.
//
// The version of the context and the version of the shading language
// are read together with the unmasked renderer, and a script that
// finds three strings that do not belong together knows that the GPU
// is a mask.  So they come from the profile as well.
//
// The strings are the whole ones -- "WebGL 2.0 (OpenGL ES 3.0
// Chromium)" -- and not the inner part, because the wrapper is what
// tells the script which API version the context implements.
std::string GetWebGLVersionString();
std::string GetWebGLShadingLanguageVersionString();

// The WebGL parameter table.
//
// The table is what identifies the GPU: the unmasked renderer says
// "Mali-G78" and the table says how many texture units it has and how
// many uniform vectors a fragment shader may declare.  A table that
// does not belong to the named renderer is the easiest way to catch a
// spoofed GPU, so the whole table comes from the profile.
//
// The parameters are looked up by their lower case names --
// ``max_texture_size`` for ``GL_MAX_TEXTURE_SIZE``.  A parameter the
// profile does not describe returns false, and the caller keeps the
// host's value.
bool GetWebGLParameter(const std::string& name, int* out);

// The parameters that are ranges rather than single values.
bool GetWebGLParameterRange(const std::string& name,
                            std::vector<double>* out);

// The viewport dimensions, which are a pair.
bool GetWebGLViewportDims(std::vector<int>* out);

// ---------------------------------------------------------------------------
// The surfaces that are read on their own.
//
// A fingerprinting script does not have to combine the canvas hash with
// the WebGL hash with the device serial.  It can read the audio
// fingerprint, the geometry of a rectangle, the media device list, the
// motion readings or the storage estimate and cluster on any one of them
// alone.  Each of those therefore needs its own accessor, and each of
// them is derived from the profile's own seed so that two profiles never
// report the same value.
// ---------------------------------------------------------------------------

// The audio fingerprint.
//
// The Web Audio API produces a device-specific fingerprint from the
// combination of an oscillator and a compressor.  The accessors return
// the profile's own sample rate, latency and channel count, and the
// renderer applies the deterministic perturbation to the rendered
// samples.
double GetAudioSampleRate();
double GetAudioBaseLatency();
double GetAudioOutputLatency();
int GetAudioChannelCount();
int GetAudioMaxChannelCount();
PixelNoiseKey GetAudioNoiseKey();

// Applies the profile's own perturbation to a block of rendered samples.
// The Web Audio fingerprint is the sum of an oscillator and a
// compressor, and the result is stable to about fifteen digits on every
// machine that runs the same build.  Two profiles that render the same
// graph therefore produce the same fingerprint unless the samples
// themselves are perturbed.
//
// The perturbation is deterministic in the sample index and in the
// profile's own salt, so the fingerprint of one profile never changes
// between two reads, and the fingerprint of two profiles never collides.
void ApplyAudioNoise(float* samples, size_t count, size_t offset);

// The geometry perturbation.
//
// The deltas are applied as integer pixel offsets, so the layout and the
// hit testing stay consistent with what the page sees.
int GetRectDeltaX();
int GetRectDeltaY();
int GetRectDeltaWidth();
int GetRectDeltaHeight();

// Applies the profile's own deltas to a rectangle.  The |surface| names
// the readback surface, so that the element rectangle and the range
// rectangle are perturbed by different amounts and therefore still look
// like two independent observations of the same layout.
void ApplyRectDeltas(gfx::RectF& rect, const char* surface);

// The media devices.
//
// The device identifiers are derived from the profile's own salt, so two
// profiles never expose the same device ids.
std::string GetMediaDeviceSalt();

// Rewrites a device identifier so that it is unique to the profile.  The
// host's identifiers are stable across every profile that runs on the
// machine, which makes them a very effective way to link two profiles
// that are supposed to be two different devices.  The rewrite keeps the
// shape of the identifier -- an Android camera id looks like a UUID --
// so that a page which validates the format does not reject it.
//
// |kind| names the device class ("audioinput", "audiooutput",
// "videoinput") and |index| is the position of the device in the host's
// list.  The pair, mixed with the profile's salt, produces the new
// identifier.
std::string RewriteMediaDeviceId(const std::string& device_id,
                                 const char* kind,
                                 size_t index);

// One device of the profile's own inventory.
//
// The profile declares the devices it has.  The host's inventory is the
// machine's: a desktop has a camera that an Android phone does not, and
// a machine with two microphones has two.  A page that counts the
// devices, or that reads their labels, learns about the machine rather
// than about the profile -- so the enumeration has to be the profile's.
//
// The identifiers here are already the profile's own: they were derived
// from the profile's salt when the profile was generated, and they are
// the same in every origin the profile visits.
struct NovaMediaDevice {
  std::string device_id;
  std::string group_id;
  std::string label;
  std::string kind;
};

// The profile's own device inventory, grouped by the device class.
//
// The list is empty when the profile does not describe an inventory, or
// when the profile asked for the host's devices to be passed through.
// In that case the caller keeps the host's list unchanged.
std::vector<std::string> NovaMediaDeviceKinds();
std::vector<NovaMediaDevice> NovaMediaDevices(const std::string& kind);

// The motion sensors.
double GetSensorAccelerometerX();
double GetSensorAccelerometerY();
double GetSensorAccelerometerZ();
double GetSensorGyroscopeX();
double GetSensorGyroscopeY();
double GetSensorGyroscopeZ();
double GetSensorGravityZ();

// The per-unit salts of the motion and the storage surfaces.  The core
// mixes them into the readings it hands out, so that two units of the
// same model do not report the same gravity or the same storage
// estimate -- the raw readings alone cannot tell them apart.
std::string GetSensorSalt();
std::string GetStorageSalt();

// The storage estimate.
uint64_t GetStorageQuotaBytes();
uint64_t GetStorageUsageBytes();

// The WebGPU adapter.
std::string GetWebGpuSalt();

// Whether a font family is one of the families the profile declares as
// installed.  The comparison is case insensitive, because the CSS font
// matching is, and the generic families -- serif, sans-serif, monospace
// and the cursive ones -- are always available.
bool IsFontInProfile(const std::string& family_name,
                     const std::vector<std::string>& installed);

// Whether the family is one of the CSS generic families.
//
// A generic family is a request for "whatever the platform offers for
// this kind of text", not a file.  It is always available and it is
// never part of the profile's inventory.
bool IsGenericFontFamily(const std::string& family_name);

// The size adjustment the profile's font inventory applies to a family.
//
// The standard font probe reads the difference between a family's
// metrics and the generic's, so a family that is not on the machine
// measures as the generic and is reported as absent.  The profile
// declares an inventory the machine does not carry, so the adjustment
// is what makes the declared families measure differently -- and the
// salt is what makes the adjustment the same for every run of the
// profile and different for every other profile.
//
// Returns 1.0 when the profile carries no salt.
double NovaFontSizeAdjustment(const std::string& family_name,
                               const std::string& salt);

// The size adjustment the profile's font inventory applies to a family,
// with the size the family is asked for taken into account.
//
// The platform rounds a font's size to whole pixels before it is
// rasterized, so an adjustment only has an effect when it moves the
// size by at least one pixel.  This function returns an adjustment that
// does move it, and reports the size the family is to be asked for
// through |applied|.
double NovaFontSizeAdjustmentForSize(const std::string& family_name,
                                     const std::string& salt,
                                     float font_size,
                                     float* applied);

// The process entry points.
bool InitializeFromCommandLine(const base::CommandLine& command_line);
bool InitializeGpuProcess(const base::CommandLine& command_line);
bool InitializeNetworkProcess(const base::CommandLine& command_line);

// ---------------------------------------------------------------------------
// The TLS handshake.
//
// The network service runs in its own process, so it has to be able to
// read the configuration on its own.  The profile is installed there
// from the command line the same way it is in the browser.
// ---------------------------------------------------------------------------

// The cipher suites the client offers, in the order it offers them.
// An empty list means the stock Chromium list.
const std::vector<std::string>& GetTlsCipherSuites();

// The supported groups the client offers, in the order it offers them.
const std::vector<std::string>& GetTlsSupportedGroups();

// The signature algorithms the client offers, in the order it offers
// them.
const std::vector<std::string>& GetTlsSignatureAlgorithms();

// The ALPN protocols the client offers, in the order it offers them.
const std::vector<std::string>& GetTlsAlpnProtocols();

// The TLS versions the client offers, from the oldest to the newest.
const std::vector<std::string>& GetTlsVersions();

// The extension order.  BoringSSL permutes the extensions by default;
// the profile fixes the order so that the handshake of two profiles can
// be told apart.
const std::vector<std::string>& GetTlsExtensionOrder();

// Whether the handshake carries the GREASE extension.
bool GetTlsGreaseEnabled();

// ---------------------------------------------------------------------------
// The JavaScript fallback layer.
//
// The native core covers the values that Blink reads.  The values that
// only JavaScript can see -- the heap limit, the maths, the codec list,
// the canvas hash -- are carried by a script that the renderer injects
// at document start.
//
// The script is handed to the browser through the --nova-js-runtime
// switch, which carries the path of the file in the user data
// directory rather than the script itself: the command line of a process
// is readable by every process on the machine, the user data directory
// is not.
//
// The file is read once, in the renderer, the first time a document asks
// for it.  A page that arrives before the file has been read gets the
// values from the native core alone, which is why the injection is
// driven from the document rather than from the browser process.
// ---------------------------------------------------------------------------

// Reads the runtime script named on the command line, if there is one.
// The result is cached, so the file is read once per renderer process.
//
// The caller must not free the result; it lives as long as the process.
const std::string& GetJsRuntimeSource();

// Whether the runtime script was found and read.  When it was not, the
// native core is the only layer, and the probe will say so.
bool HasJsRuntime();

// The script the renderer actually runs.
//
// It is the runtime script with the native core's declaration of the
// surfaces it owns in front of it.  The declaration has to run first:
// the runtime reads it as it starts and stands down on the surfaces the
// native core has already taken care of.
//
// The caller must not free the result; it lives as long as the process.
const std::string& GetEffectiveJsRuntimeSource();

// Hands the runtime to the renderer over the Mojo channel.
//
// This is how the script normally arrives.  The command line cannot
// carry it: a Windows command line holds at most 32,767 characters and
// the script is longer than that, so the argument would arrive
// truncated and the renderer would run half a script.
//
// The script that arrives here is the whole script, so it wins over
// anything the command line carried.
void SetJsRuntimeFromMojo(std::string runtime);

// ---------------------------------------------------------------------------
// Handing the runtime to the renderer.
//
// The renderer is sandboxed: it cannot open a file in the user data
// directory, so it cannot read the runtime for itself.  The browser
// process can, and it does.
//
// The browser therefore reads the script and passes it to the renderer
// on the command line.  That puts the payload in the arguments of the
// renderer, where every process on the machine can read it -- which is
// exactly what writing the file into the user data directory was
// meant to avoid.
//
// The trade is made deliberately: a fingerprint that a site can read
// out of the process arguments is a fingerprint that has already
// leaked, but a fingerprint that never reaches the renderer is not a
// fingerprint at all.  The payload only reaches the renderer when the
// profile is being launched, and the renderer is short lived.
// ---------------------------------------------------------------------------

// The layer's code, compiled into the browser.
//
// The code is the same for every profile, so it does not travel at all:
// it is in the binary.  Only the payload -- the profile's values -- is
// handed over, and it is small enough to fit on a command line.
const std::string& GetJsRuntimeCode();

// The name of the switch that carries the profile's payload to the
// renderer.
//
// The payload is what the layer reads: the user agent, the screen, the
// device catalogue, the codec list.  It is different for every profile
// and it is the only part of the layer that is secret.
extern const char kJsPayloadSwitchName[];

}  // namespace nova

#endif  // NOVA_PROFILE_CORE_CHROMIUM_HOOKS_H_
