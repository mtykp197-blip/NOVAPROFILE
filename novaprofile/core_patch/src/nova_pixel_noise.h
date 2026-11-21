// NOVA Profile Core -- deterministic pixel noise.
//
// This is the heart of the graphics impersonation.  Two properties are
// non-negotiable:
//
//  1. **Determinism**: the same profile always produces the same pixels.
//     The noise is derived from (master_seed, salt, renderer, nudge_step)
//     through HKDF-SHA256, so a profile that is closed and reopened emits
//     exactly the same image hash as before.
//
//  2. **Separation**: two different profiles never produce the same pixels.
//     The renderer string is mixed into the hash input, so two different
//     GPU models cannot accidentally collide.
//
// The noise is applied at the Skia readback layer, which is below Blink.
// That means it covers every path that can observe the pixels:
//
//   - CanvasRenderingContext2D.toDataURL() / toBlob()
//   - CanvasRenderingContext2D.getImageData()
//   - OffscreenCanvas.convertToBlob()
//   - WebGLRenderingContext.readPixels()  (including the WebGL2 path)
//   - ImageBitmap / createImageBitmap round trips
//
// and it covers them in *every* execution context, because the renderer
// process holds one config and the noise key is derived from it rather
// than from a per-frame counter.

#ifndef NOVA_PROFILE_CORE_PIXEL_NOISE_H_
#define NOVA_PROFILE_CORE_PIXEL_NOISE_H_

#include <cstdint>
#include <string>
#include <vector>

#include "nova_core_config.h"

namespace nova {

// A 256-bit key that seeds the pixel noise for one (profile, surface) pair.
struct PixelNoiseKey {
  uint8_t bytes[32] = {};

  bool operator==(const PixelNoiseKey& other) const;
  bool operator!=(const PixelNoiseKey& other) const { return !(*this == other); }
};

// Derives the noise key for a surface.
//
// |surface| names the readback path ("canvas2d", "webgl", "offscreen").
// Splitting by surface keeps the canvas hash and the WebGL hash
// independent, so a script that compares the two cannot link them.
PixelNoiseKey DerivePixelNoiseKey(const SeedConfig& seed,
                                   const std::string& surface,
                                   const std::string& salt,
                                   const std::string& renderer);

// The deterministic double that scales the noise.
//
// Mirrors SeedDeriver.derive_webgl_image_value() on the Python side: the
// value carries at least 17 significant digits so that it survives the
// round trip through a double and a JSON string without rounding.
double WebglImageValue(const SeedConfig& seed, const std::string& renderer);

// Applies the noise to a pixel buffer in place.
//
// The perturbation is deliberately tiny and *structural* rather than
// random: the same input buffer always produces the same output buffer,
// and the magnitude stays inside the tolerance of the anti-bot vendors
// that compare a canvas render against a reference render.
//
// |data|      the pixel bytes (RGBA, 4 bytes per pixel)
// |stride|    the number of bytes between two consecutive rows
// |width|     the width in pixels
// |height|    the height in pixels
// |key|       the noise key
// |strength|  0.0 leaves the buffer untouched; 1.0 is the maximum
//             perturbation
void ApplyPixelNoise(uint8_t* data,
                      size_t stride,
                      int width,
                      int height,
                      const PixelNoiseKey& key,
                      double strength);

// The same perturbation, expressed as an offset for a single pixel.
//
// Exposed so that the ANGLE readback path can perturb the value it is
// about to hand back without copying the whole buffer.
int8_t PixelNoiseOffset(const PixelNoiseKey& key, int x, int y, int channel);

}  // namespace nova

#endif  // NOVA_PROFILE_CORE_PIXEL_NOISE_H_
