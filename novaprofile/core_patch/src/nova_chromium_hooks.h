// NOVA Profile Core -- the Chromium integration patches.
//
// This header is included by the Chromium source files that need to serve
// the spoofed values.  It is deliberately tiny: a single inline function
// per patched accessor, so the diff against the Chromium tree stays
// reviewable.
//
//   third_party/blink/renderer/core/html/navigator_id.cc
//   third_party/blink/renderer/core/html/navigator_ua_data.cc
//   third_party/blink/renderer/core/screen/screen.cc
//   third_party/blink/renderer/modules/webgl/webgl_rendering_context.cc
//   third_party/blink/renderer/core/canvas/canvas_rendering_context_2d.cc
//   content/browser/browser_main_loop.cc
//   content/gpu/gpu_main.cc
//   content/browser/net/browser_context.cc

#ifndef NOVA_PROFILE_CORE_CHROMIUM_HOOKS_H_
#define NOVA_PROFILE_CORE_CHROMIUM_HOOKS_H_

#include "nova_core_config.h"
#include "nova_pixel_noise.h"

#include <cstdint>
#include <string>

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

// The geometry perturbation.
//
// The deltas are applied as integer pixel offsets, so the layout and the
// hit testing stay consistent with what the page sees.
int GetRectDeltaX();
int GetRectDeltaY();
int GetRectDeltaWidth();
int GetRectDeltaHeight();

// The media devices.
//
// The device identifiers are derived from the profile's own salt, so two
// profiles never expose the same device ids.
std::string GetMediaDeviceSalt();

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

// The process entry points.
bool InitializeFromCommandLine(const base::CommandLine& command_line);
bool InitializeGpuProcess(const base::CommandLine& command_line);
bool InitializeNetworkProcess(const base::CommandLine& command_line);

}  // namespace nova

#endif  // NOVA_PROFILE_CORE_CHROMIUM_HOOKS_H_
