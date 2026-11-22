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

#include <cstdint>
#include <string>
#include <vector>

#include "base/command_line.h"
#include "ui/gfx/geometry/rect_f.h"

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

// The noise keys, one per readback surface.
PixelNoiseKey GetCanvasNoiseKey();
PixelNoiseKey GetWebGLNoiseKey();
PixelNoiseKey GetOffscreenNoiseKey();

// The WebGL identity strings.
std::string GetUnmaskedVendorString();
std::string GetUnmaskedRendererString();

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

}  // namespace nova

#endif  // NOVA_PROFILE_CORE_CHROMIUM_HOOKS_H_
