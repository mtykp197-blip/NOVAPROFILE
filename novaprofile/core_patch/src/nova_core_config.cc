// NOVA Profile Core -- configuration parsing (implementation).

#include "nova_core_config.h"

#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "base/base64.h"
#include "base/command_line.h"
#include "base/containers/span.h"
#include "base/files/file_path.h"
#include "base/files/file_util.h"
#include "base/json/json_reader.h"
#include "base/logging.h"
#include "base/path_service.h"
#include "base/values.h"
#include "url/gurl.h"
#include "third_party/blink/renderer/core/nova/nova_transport.h"

namespace nova {
namespace {

// Small helpers for reading a JSON value into a typed field.  Each helper
// returns false when the key is missing or has the wrong shape, which makes
// the parser strict: a profile that is missing a field is rejected rather
// than silently half-applied.

bool ReadString(const base::DictValue& dict,
                const char* key,
                std::string* out,
                std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_string()) {
    *error = std::string("missing or non-string field: ") + key;
    return false;
  }
  *out = value->GetString();
  return true;
}

// A missing or misshaped field leaves |out| alone, so the caller seeds it
// with the value it wants to fall back to.  The signature matches the
// strict readers above.
bool ReadStringOrDefault(const base::DictValue& dict,
                          const char* key,
                          std::string* out,
                          std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value) {
    return true;
  }
  if (!value->is_string()) {
    *error = std::string("non-string field: ") + key;
    return false;
  }
  *out = value->GetString();
  return true;
}

bool ReadInt(const base::DictValue& dict,
             const char* key,
             int* out,
             std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_int()) {
    *error = std::string("missing or non-integer field: ") + key;
    return false;
  }
  *out = value->GetInt();
  return true;
}

// The seed epoch and the nudge step are unsigned, so they need their own
// overload: an ``int*`` cannot bind to a ``uint32_t*``.
bool ReadInt(const base::DictValue& dict,
             const char* key,
             uint32_t* out,
             std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_int()) {
    *error = std::string("missing or non-integer field: ") + key;
    return false;
  }
  *out = static_cast<uint32_t>(value->GetInt());
  return true;
}

bool ReadIntOrDefault(const base::DictValue& dict,
                       const char* key,
                       int* out,
                       std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value) {
    return true;
  }
  if (!value->is_int()) {
    *error = std::string("non-integer field: ") + key;
    return false;
  }
  *out = value->GetInt();
  return true;
}

bool ReadDouble(const base::DictValue& dict,
                const char* key,
                double* out,
                std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_double() && !value->is_int()) {
    *error = std::string("missing or non-numeric field: ") + key;
    return false;
  }
  *out = value->GetDouble();
  return true;
}

bool ReadBool(const base::DictValue& dict,
              const char* key,
              bool* out,
              std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_bool()) {
    *error = std::string("missing or non-boolean field: ") + key;
    return false;
  }
  *out = value->GetBool();
  return true;
}

bool ReadBoolOrDefault(const base::DictValue& dict,
                       const char* key,
                       bool* out,
                       std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value) {
    return true;
  }
  if (!value->is_bool()) {
    *error = std::string("non-boolean field: ") + key;
    return false;
  }
  *out = value->GetBool();
  return true;
}

bool ReadStringList(const base::DictValue& dict,
                    const char* key,
                    std::vector<std::string>* out,
                    std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_list()) {
    *error = std::string("missing or non-list field: ") + key;
    return false;
  }
  out->clear();
  for (const base::Value& item : value->GetList()) {
    if (!item.is_string()) {
      *error = std::string("non-string item in list field: ") + key;
      return false;
    }
    out->push_back(item.GetString());
  }
  return true;
}

bool ReadIntList(const base::DictValue& dict,
                 const char* key,
                 std::vector<int>* out,
                 std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_list()) {
    *error = std::string("missing or non-list field: ") + key;
    return false;
  }
  out->clear();
  for (const base::Value& item : value->GetList()) {
    if (!item.is_int()) {
      *error = std::string("non-integer item in list field: ") + key;
      return false;
    }
    out->push_back(item.GetInt());
  }
  return true;
}

bool ReadDoubleList(const base::DictValue& dict,
                     const char* key,
                     std::vector<double>* out,
                     std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_list()) {
    *error = std::string("missing or non-list field: ") + key;
    return false;
  }
  out->clear();
  for (const base::Value& item : value->GetList()) {
    if (!item.is_double() && !item.is_int()) {
      *error = std::string("non-numeric item in list field: ") + key;
      return false;
    }
    out->push_back(item.GetDouble());
  }
  return true;
}

const base::DictValue* FindDict(const base::DictValue& parent,
                                   const char* key,
                                   std::string* error) {
  const base::Value* value = parent.Find(key);
  if (!value || !value->is_dict()) {
    *error = std::string("missing or non-object field: ") + key;
    return nullptr;
  }
  return &value->GetDict();
}

bool ReadStringMap(const base::DictValue& dict,
                   const char* key,
                   std::map<std::string, std::string>* out,
                   std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_dict()) {
    *error = std::string("missing or non-object field: ") + key;
    return false;
  }
  out->clear();
  for (const auto& pair : value->GetDict()) {
    if (!pair.second.is_string()) {
      *error = std::string("non-string value in map field: ") + key;
      return false;
    }
    out->emplace(pair.first, pair.second.GetString());
  }
  return true;
}

bool ReadDoubleMap(const base::DictValue& dict,
                   const char* key,
                   std::map<std::string, double>* out,
                   std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_dict()) {
    *error = std::string("missing or non-object field: ") + key;
    return false;
  }
  out->clear();
  for (const auto& pair : value->GetDict()) {
    if (!pair.second.is_double() && !pair.second.is_int()) {
      *error = std::string("non-numeric value in map field: ") + key;
      return false;
    }
    out->emplace(pair.first, pair.second.GetDouble());
  }
  return true;
}

// Reads a vector of {brand, version} objects.
bool ReadBrandList(const base::DictValue& dict,
                   const char* key,
                   std::vector<BrandVersion>* out,
                   std::string* error) {
  const base::Value* value = dict.Find(key);
  if (!value || !value->is_list()) {
    *error = std::string("missing or non-list field: ") + key;
    return false;
  }
  out->clear();
  for (const base::Value& item : value->GetList()) {
    if (!item.is_dict()) {
      *error = std::string("non-object item in list field: ") + key;
      return false;
    }
    BrandVersion brand;
    if (!ReadString(item.GetDict(), "brand", &brand.brand, error) ||
        !ReadString(item.GetDict(), "version", &brand.version, error)) {
      return false;
    }
    out->push_back(brand);
  }
  return true;
}

bool ReadShaderPrecision(const base::DictValue& dict,
                         ShaderPrecision* out,
                         std::string* error) {
  const base::DictValue* vertex = FindDict(dict, "vertex_shader", error);
  if (!vertex) return false;
  const base::DictValue* fragment = FindDict(dict, "fragment_shader", error);
  if (!fragment) return false;

  auto read_stage = [&](const base::DictValue& stage,
                         std::map<std::string, ShaderPrecisionRange>* target) {
    for (const auto& pair : stage) {
      if (!pair.second.is_dict()) {
        *error = "non-object entry in a shader precision table";
        return false;
      }
      ShaderPrecisionRange range;
      if (!ReadInt(pair.second.GetDict(), "range_min", &range.range_min, error) ||
          !ReadInt(pair.second.GetDict(), "range_max", &range.range_max, error) ||
          !ReadInt(pair.second.GetDict(), "precision", &range.precision, error)) {
        return false;
      }
      target->emplace(pair.first, range);
    }
    return true;
  };

  return read_stage(*vertex, &out->vertex_shader) &&
         read_stage(*fragment, &out->fragment_shader);
}

// ---------------------------------------------------------------------------
// Section parsers
// ---------------------------------------------------------------------------

bool ParseSeed(const base::DictValue& dict, SeedConfig* out, std::string* error) {
  // The stratum is optional.  It is read separately from the rest,
  // because "absent" and "zero" are different things: a stratum of zero
  // is the first unit of a family, and a profile that does not belong
  // to a family at all has no stratum.
  //
  // The value arrives either as a number or as a null -- the control
  // plane writes a null for a profile that stands alone, and the
  // parser has to tell the two apart without the reader's help.
  const base::Value* stratum = dict.Find("stratum");
  if (stratum) {
    switch (stratum->type()) {
      case base::Value::Type::INTEGER: {
        const int64_t value = stratum->GetInt();
        if (value < 0) {
          *error = "negative field: stratum";
          return false;
        }
        out->has_stratum = true;
        out->stratum = static_cast<uint32_t>(value);
        break;
      }
      case base::Value::Type::NONE:
        // The absent stratum: the profile stands alone.
        break;
      case base::Value::Type::STRING:
        // The control plane spells "absent" as an empty string when it
        // has had to turn the null into one.  It means the same thing.
        if (!stratum->GetString().empty()) {
          *error = "non-integer field: stratum";
          return false;
        }
        break;
      default:
        *error = "non-integer field: stratum";
        return false;
    }
  }

  return ReadString(dict, "master_seed", &out->master_seed, error) &&
         ReadInt(dict, "seed_epoch", &out->seed_epoch, error) &&
         ReadInt(dict, "nudge_step", &out->nudge_step, error) &&
         ReadString(dict, "hkdf_version", &out->hkdf_version, error) &&
         ReadString(dict, "preset_id", &out->preset_id, error) &&
         ReadString(dict, "core_version", &out->core_version, error);
}

bool ParseAndroidBuild(const base::DictValue& dict,
                       AndroidBuild* out,
                       std::string* error) {
  return ReadString(dict, "version_release", &out->version_release, error) &&
         ReadInt(dict, "sdk_int", &out->sdk_int, error) &&
         ReadString(dict, "security_patch", &out->security_patch, error) &&
         ReadString(dict, "build_id", &out->build_id, error) &&
         ReadString(dict, "display", &out->display, error) &&
         ReadString(dict, "version_incremental", &out->version_incremental, error) &&
         ReadString(dict, "model", &out->model, error) &&
         ReadString(dict, "device", &out->device, error) &&
         ReadString(dict, "product", &out->product, error) &&
         ReadString(dict, "manufacturer", &out->manufacturer, error) &&
         ReadString(dict, "brand", &out->brand, error) &&
         ReadString(dict, "hardware", &out->hardware, error) &&
         ReadString(dict, "board", &out->board, error) &&
         ReadString(dict, "bootloader", &out->bootloader, error) &&
         ReadString(dict, "fingerprint", &out->fingerprint, error) &&
         ReadString(dict, "host", &out->host, error) &&
         ReadString(dict, "user", &out->user, error) &&
         ReadString(dict, "tags", &out->tags, error) &&
         ReadString(dict, "type", &out->type, error) &&
         ReadString(dict, "serial", &out->serial, error);
}

bool ParseAndroidAbiSoc(const base::DictValue& dict,
                        AndroidAbiSoc* out,
                        std::string* error) {
  return ReadStringList(dict, "supported_abis", &out->supported_abis, error) &&
         ReadStringList(dict, "supported_64_bit_abis",
                        &out->supported_64_bit_abis, error) &&
         ReadStringList(dict, "supported_32_bit_abis",
                        &out->supported_32_bit_abis, error) &&
         ReadString(dict, "primary_abi", &out->primary_abi, error) &&
         ReadBool(dict, "is_64_bit", &out->is_64_bit, error) &&
         ReadString(dict, "soc_vendor", &out->soc_vendor, error) &&
         ReadString(dict, "soc_model", &out->soc_model, error) &&
         ReadString(dict, "cpu_cluster_layout", &out->cpu_cluster_layout, error) &&
         ReadString(dict, "gpu_architecture", &out->gpu_architecture, error);
}

bool ParseScreen(const base::DictValue& dict, Screen* out, std::string* error) {
  if (!ReadInt(dict, "width", &out->width, error) ||
      !ReadInt(dict, "height", &out->height, error) ||
      !ReadInt(dict, "avail_width", &out->avail_width, error) ||
      !ReadInt(dict, "avail_height", &out->avail_height, error) ||
      !ReadInt(dict, "avail_left", &out->avail_left, error) ||
      !ReadInt(dict, "avail_top", &out->avail_top, error) ||
      !ReadInt(dict, "color_depth", &out->color_depth, error) ||
      !ReadInt(dict, "pixel_depth", &out->pixel_depth, error) ||
      !ReadDouble(dict, "device_pixel_ratio", &out->device_pixel_ratio, error) ||
      !ReadInt(dict, "density_dpi", &out->density_dpi, error)) {
    return false;
  }

  // The panel ratios are optional: a profile that does not name them
  // simply reports the ratio it states.
  std::vector<double> ratios;
  std::string ratio_error;
  if (ReadDoubleList(dict, "device_pixel_ratio_options", &ratios, &ratio_error)) {
    out->device_pixel_ratio_options = ratios;
  }
  return true;
}

bool ParseOrientation(const base::DictValue& dict,
                      Orientation* out,
                      std::string* error) {
  return ReadString(dict, "type", &out->type, error) &&
         ReadInt(dict, "angle", &out->angle, error) &&
         ReadBool(dict, "lock_supported", &out->lock_supported, error);
}

bool ParseViewport(const base::DictValue& dict,
                   Viewport* out,
                   std::string* error) {
  const base::DictValue* insets = FindDict(dict, "safe_area_insets", error);
  if (!insets) return false;
  return ReadInt(dict, "inner_width", &out->inner_width, error) &&
         ReadInt(dict, "inner_height", &out->inner_height, error) &&
         ReadInt(dict, "outer_width", &out->outer_width, error) &&
         ReadInt(dict, "outer_height", &out->outer_height, error) &&
         ReadInt(dict, "screen_x", &out->screen_x, error) &&
         ReadInt(dict, "screen_y", &out->screen_y, error) &&
         ReadDouble(dict, "visual_viewport_scale",
                    &out->visual_viewport_scale, error) &&
         ReadInt(*insets, "top", &out->safe_area_insets.top, error) &&
         ReadInt(*insets, "right", &out->safe_area_insets.right, error) &&
         ReadInt(*insets, "bottom", &out->safe_area_insets.bottom, error) &&
         ReadInt(*insets, "left", &out->safe_area_insets.left, error) &&
         ReadInt(dict, "scrollbar_width", &out->scrollbar_width, error);
}

bool ParseMediaQueries(const base::DictValue& dict,
                       MediaQueries* out,
                       std::string* error) {
  return ReadString(dict, "prefers_color_scheme", &out->prefers_color_scheme,
                    error) &&
         ReadString(dict, "prefers_reduced_motion", &out->prefers_reduced_motion,
                    error) &&
         ReadString(dict, "prefers_contrast", &out->prefers_contrast, error) &&
         ReadString(dict, "forced_colors", &out->forced_colors, error) &&
         ReadString(dict, "pointer", &out->pointer, error) &&
         ReadStringList(dict, "any_pointer", &out->any_pointer, error) &&
         ReadString(dict, "hover", &out->hover, error) &&
         ReadStringList(dict, "any_hover", &out->any_hover, error) &&
         ReadString(dict, "color_gamut", &out->color_gamut, error) &&
         ReadString(dict, "dynamic_range", &out->dynamic_range, error);
}

bool ParseCanvas(const base::DictValue& dict,
                 Canvas2DConfig* out,
                 std::string* error) {
  return ReadString(dict, "mode", &out->mode, error) &&
         ReadString(dict, "salt", &out->salt, error) &&
         ReadDouble(dict, "noise_strength", &out->noise_strength, error) &&
         ReadBool(dict, "persist_across_restarts", &out->persist_across_restarts,
                  error) &&
         ReadStringList(dict, "targets", &out->targets, error);
}

bool ParseWebGL(const base::DictValue& dict,
                WebGLConfig* out,
                std::string* error) {
  const base::DictValue* noise = FindDict(dict, "image_noise", error);
  if (!noise) return false;
  const base::DictValue* extensions = FindDict(dict, "extensions", error);
  if (!extensions) return false;
  const base::DictValue* parameters = FindDict(dict, "parameters", error);
  if (!parameters) return false;
  const base::DictValue* precision = FindDict(dict, "shader_precision", error);
  if (!precision) return false;

  if (!ReadString(*noise, "value", &out->image_noise.value, error) ||
      !ReadBool(*noise, "enabled", &out->image_noise.enabled, error)) {
    return false;
  }
  if (!ReadStringList(*extensions, "supported", &out->extensions,
                      error)) {
    return false;
  }

  const base::DictValue& p = *parameters;
  WebGLParameters& params = out->parameters;
  bool ok = ReadInt(p, "max_texture_size", &params.max_texture_size, error) &&
            ReadInt(p, "max_cube_map_texture_size",
                    &params.max_cube_map_texture_size, error) &&
            ReadInt(p, "max_3d_texture_size", &params.max_3d_texture_size,
                    error) &&
            ReadInt(p, "max_array_texture_layers",
                    &params.max_array_texture_layers, error) &&
            ReadInt(p, "max_renderbuffer_size", &params.max_renderbuffer_size,
                    error) &&
            ReadIntList(p, "max_viewport_dims", &params.max_viewport_dims,
                        error) &&
            ReadInt(p, "max_vertex_attribs", &params.max_vertex_attribs, error) &&
            ReadInt(p, "max_vertex_uniform_vectors",
                    &params.max_vertex_uniform_vectors, error) &&
            ReadInt(p, "max_vertex_uniform_components",
                    &params.max_vertex_uniform_components, error) &&
            ReadInt(p, "max_fragment_uniform_vectors",
                    &params.max_fragment_uniform_vectors, error) &&
            ReadInt(p, "max_fragment_uniform_components",
                    &params.max_fragment_uniform_components, error) &&
            ReadInt(p, "max_varying_vectors", &params.max_varying_vectors, error) &&
            ReadInt(p, "max_combined_texture_image_units",
                    &params.max_combined_texture_image_units, error) &&
            ReadInt(p, "max_vertex_texture_image_units",
                    &params.max_vertex_texture_image_units, error) &&
            ReadInt(p, "max_texture_image_units", &params.max_texture_image_units,
                    error) &&
            ReadInt(p, "max_color_attachments", &params.max_color_attachments,
                    error) &&
            ReadInt(p, "max_draw_buffers", &params.max_draw_buffers, error) &&
            ReadInt(p, "max_samples", &params.max_samples, error) &&
            ReadInt(p, "max_uniform_buffer_bindings",
                    &params.max_uniform_buffer_bindings, error) &&
            ReadDoubleList(p, "aliased_line_width_range",
                           &params.aliased_line_width_range, error) &&
            ReadDoubleList(p, "aliased_point_size_range",
                           &params.aliased_point_size_range, error) &&
            ReadInt(p, "sample_buffers", &params.sample_buffers, error) &&
            ReadInt(p, "samples", &params.samples, error) &&
            ReadInt(p, "max_texture_max_anisotropy_ext",
                    &params.max_texture_max_anisotropy_ext, error);
  if (!ok) {
    return false;
  }

  return ReadString(dict, "mode", &out->mode, error) &&
         ReadString(dict, "unmasked_vendor", &out->unmasked_vendor, error) &&
         ReadString(dict, "unmasked_renderer", &out->unmasked_renderer, error) &&
         ReadString(dict, "vendor", &out->vendor, error) &&
         ReadString(dict, "renderer", &out->renderer, error) &&
         ReadString(dict, "version", &out->version, error) &&
         ReadString(dict, "shading_language_version",
                    &out->shading_language_version, error) &&
         ReadBool(dict, "read_pixels_sync", &out->read_pixels_sync, error) &&
         ReadShaderPrecision(*precision, &out->shader_precision,
                             error) &&
         ReadStringOrDefault(dict, "salt", &out->salt, error);
}

bool ParseWebGPU(const base::DictValue& dict,
                 WebGPUConfig* out,
                 std::string* error) {
  const base::DictValue* adapter = FindDict(dict, "adapter", error);
  if (!adapter) return false;
  return ReadBool(dict, "enabled", &out->enabled, error) &&
         ReadString(*adapter, "vendor", &out->adapter.vendor, error) &&
         ReadString(*adapter, "architecture",
                    &out->adapter.architecture, error) &&
         ReadString(*adapter, "device", &out->adapter.device, error) &&
         ReadString(*adapter, "description",
                    &out->adapter.description, error) &&
         ReadBool(*adapter, "is_fallback_adapter",
                  &out->adapter.is_fallback_adapter, error) &&
         ReadStringList(dict, "features", &out->features, error) &&
         ReadDoubleMap(dict, "limits", &out->limits, error) &&
         ReadStringOrDefault(dict, "salt", &out->salt, error);
}

bool ParseAudio(const base::DictValue& dict, AudioConfig* out, std::string* error) {
  return ReadString(dict, "mode", &out->mode, error) &&
         ReadString(dict, "salt", &out->salt, error) &&
         ReadInt(dict, "sample_rate", &out->sample_rate, error) &&
         ReadDouble(dict, "base_latency", &out->base_latency, error) &&
         ReadDouble(dict, "output_latency", &out->output_latency, error) &&
         ReadInt(dict, "channel_count", &out->channel_count, error) &&
         ReadInt(dict, "max_channel_count", &out->max_channel_count, error) &&
         ReadBool(dict, "respect_core_quantization",
                  &out->respect_core_quantization, error) &&
         ReadDouble(dict, "noise_strength", &out->noise_strength, error);
}

bool ParseRects(const base::DictValue& dict, RectsConfig* out, std::string* error) {
  return ReadString(dict, "mode", &out->mode, error) &&
         ReadString(dict, "salt", &out->salt, error) &&
         ReadInt(dict, "delta_x", &out->delta_x, error) &&
         ReadInt(dict, "delta_y", &out->delta_y, error) &&
         ReadInt(dict, "delta_width", &out->delta_width, error) &&
         ReadInt(dict, "delta_height", &out->delta_height, error) &&
         ReadStringList(dict, "targets", &out->targets, error);
}

bool ParseFonts(const base::DictValue& dict, FontsConfig* out, std::string* error) {
  return ReadString(dict, "mode", &out->mode, error) &&
         ReadStringList(dict, "installed", &out->installed, error) &&
         ReadString(dict, "salt", &out->salt, error) &&
         ReadBool(dict, "preserve_metric_clusters",
                  &out->preserve_metric_clusters, error);
}

bool ParseMediaDevices(const base::DictValue& dict,
                       MediaDevicesConfig* out,
                       std::string* error) {
  auto read_entries = [&](const char* key,
                           std::vector<MediaDeviceEntry>* target) {
    const base::Value* value = dict.Find(key);
    if (!value || !value->is_list()) {
      *error = std::string("missing or non-list field: ") + key;
      return false;
    }
    target->clear();
    for (const base::Value& item : value->GetList()) {
      if (!item.is_dict()) {
        *error = std::string("non-object item in list field: ") + key;
        return false;
      }
      MediaDeviceEntry entry;
      const base::DictValue& item_dict = item.GetDict();
      if (!ReadString(item_dict, "device_id", &entry.device_id, error) ||
          !ReadString(item_dict, "group_id", &entry.group_id, error) ||
          !ReadString(item_dict, "label", &entry.label, error)) {
        return false;
      }
      ReadStringOrDefault(item_dict, "facing_mode", &entry.facing_mode, error);
      target->push_back(entry);
    }
    return true;
  };

  if (!ReadString(dict, "mode", &out->mode, error) ||
      !ReadString(dict, "id_scope", &out->id_scope, error) ||
      !ReadString(dict, "salt", &out->salt, error)) {
    return false;
  }
  return read_entries("audio_inputs", &out->audio_inputs) &&
         read_entries("audio_outputs", &out->audio_outputs) &&
         read_entries("video_inputs", &out->video_inputs);
}

bool ParseStorageNetwork(const base::DictValue& dict,
                         StorageNetworkConfig* out,
                         std::string* error) {
  const base::Value* quota = dict.Find("quota_bytes");
  const base::Value* usage = dict.Find("usage_bytes");
  if (!quota || !usage || !quota->is_int() || !usage->is_int()) {
    *error = "missing or non-integer field: quota_bytes/usage_bytes";
    return false;
  }
  out->quota_bytes = static_cast<uint64_t>(quota->GetInt());
  out->usage_bytes = static_cast<uint64_t>(usage->GetInt());
  return ReadBool(dict, "persisted_default", &out->persisted_default, error) &&
         ReadString(dict, "effective_type", &out->effective_type, error) &&
         ReadInt(dict, "rtt", &out->rtt, error) &&
         ReadDouble(dict, "downlink", &out->downlink, error) &&
         ReadBool(dict, "save_data", &out->save_data, error) &&
         ReadString(dict, "connection_type", &out->connection_type, error) &&
         ReadStringOrDefault(dict, "salt", &out->salt, error);
}

bool ParseBatterySpeechGamepad(const base::DictValue& dict,
                                BatterySpeechGamepad* out,
                                std::string* error) {
  const base::DictValue* battery = FindDict(dict, "battery", error);
  if (!battery) return false;
  const base::DictValue* gamepad = FindDict(dict, "gamepad", error);
  if (!gamepad) return false;

  if (!ReadBool(*battery, "enabled", &out->battery.enabled, error) ||
      !ReadBool(*battery, "charging", &out->battery.charging, error) ||
      !ReadDouble(*battery, "level", &out->battery.level, error) ||
      !ReadInt(*battery, "charging_time", &out->battery.charging_time,
               error) ||
      !ReadInt(*battery, "discharging_time",
               &out->battery.discharging_time, error)) {
    return false;
  }

  if (!ReadBool(*gamepad, "enabled", &out->gamepad.enabled, error)) {
    return false;
  }

  const base::Value* voices = dict.Find("speech_voices");
  if (!voices || !voices->is_list()) {
    *error = "missing or non-list field: speech_voices";
    return false;
  }
  for (const base::Value& item : voices->GetList()) {
    if (!item.is_dict()) {
      *error = "non-object item in list field: speech_voices";
      return false;
    }
    SpeechVoice voice;
    voice.is_default = false;
    voice.local_service = true;
    const base::DictValue& voice_dict = item.GetDict();
    if (!ReadString(voice_dict, "name", &voice.name, error) ||
        !ReadString(voice_dict, "lang", &voice.lang, error)) {
      return false;
    }
    ReadBoolOrDefault(voice_dict, "default", &voice.is_default, error);
    ReadBoolOrDefault(voice_dict, "local_service", &voice.local_service, error);
    ReadStringOrDefault(voice_dict, "voice_uri", &voice.voice_uri, error);
    out->speech_voices.push_back(voice);
  }
  return true;
}

bool ParseSensors(const base::DictValue& dict,
                  SensorsConfig* out,
                  std::string* error) {
  const base::DictValue* accel = FindDict(dict, "accelerometer", error);
  if (!accel) return false;
  const base::DictValue* gyro = FindDict(dict, "gyroscope", error);
  if (!gyro) return false;
  const base::DictValue* linear = FindDict(dict, "linear_acceleration", error);
  if (!linear) return false;
  const base::DictValue* gravity = FindDict(dict, "gravity", error);
  if (!gravity) return false;
  const base::DictValue* absolute = FindDict(dict, "absolute_orientation", error);
  if (!absolute) return false;
  const base::DictValue* relative = FindDict(dict, "relative_orientation", error);
  if (!relative) return false;

  auto read_vector = [&](const base::DictValue& source, Vector3D* target) {
    return ReadDouble(source, "x", &target->x, error) &&
           ReadDouble(source, "y", &target->y, error) &&
           ReadDouble(source, "z", &target->z, error);
  };
  auto read_orientation = [&](const base::DictValue& source,
                              Orientation3D* target) {
    return ReadDouble(source, "alpha", &target->alpha, error) &&
           ReadDouble(source, "beta", &target->beta, error) &&
           ReadDouble(source, "gamma", &target->gamma, error);
  };

  return ReadBool(dict, "enabled", &out->enabled, error) &&
         ReadInt(dict, "sampling_hz", &out->sampling_hz, error) &&
         ReadString(dict, "motion_profile", &out->motion_profile, error) &&
         read_vector(*accel, &out->accelerometer) &&
         read_vector(*gyro, &out->gyroscope) &&
         read_vector(*linear, &out->linear_acceleration) &&
         read_vector(*gravity, &out->gravity) &&
         read_orientation(*absolute, &out->absolute_orientation) &&
         read_orientation(*relative, &out->relative_orientation) &&
         ReadStringOrDefault(dict, "salt", &out->salt, error);
}

bool ParseNavigator(const base::DictValue& dict,
                    NavigatorConfig* out,
                    std::string* error) {
  // The hardware ranges are optional: a profile that does not name them
  // simply reports the values it states, and the core does not derive
  // anything on its behalf.
  std::vector<int> cores;
  std::vector<int> memory;
  std::vector<int> touch;
  bool has_cores = ReadIntList(dict, "hardware_concurrency_options", &cores, error);
  bool has_memory = ReadIntList(dict, "device_memory_options", &memory, error);
  bool has_touch = ReadIntList(dict, "max_touch_points_options", &touch, error);

  if (!ReadString(dict, "user_agent", &out->user_agent, error) ||
      !ReadString(dict, "app_version", &out->app_version, error) ||
      !ReadString(dict, "app_name", &out->app_name, error) ||
      !ReadString(dict, "app_code_name", &out->app_code_name, error) ||
      !ReadString(dict, "vendor", &out->vendor, error) ||
      !ReadString(dict, "vendor_sub", &out->vendor_sub, error) ||
      !ReadString(dict, "product", &out->product, error) ||
      !ReadString(dict, "product_sub", &out->product_sub, error) ||
      !ReadString(dict, "platform", &out->platform, error) ||
      !ReadInt(dict, "hardware_concurrency", &out->hardware_concurrency,
               error) ||
      !ReadInt(dict, "device_memory", &out->device_memory, error) ||
      !ReadInt(dict, "max_touch_points", &out->max_touch_points, error) ||
      !ReadString(dict, "language", &out->language, error) ||
      !ReadStringList(dict, "languages", &out->languages, error) ||
      !ReadBool(dict, "cookie_enabled", &out->cookie_enabled, error) ||
      !ReadBool(dict, "pdf_viewer_enabled", &out->pdf_viewer_enabled, error) ||
      !ReadBool(dict, "webdriver", &out->webdriver, error) ||
      !ReadBool(dict, "on_line", &out->on_line, error) ||
      !ReadBool(dict, "java_enabled", &out->java_enabled, error)) {
    return false;
  }

  if (has_cores) {
    out->hardware_concurrency_options = cores;
  }
  if (has_memory) {
    out->device_memory_options = memory;
  }
  if (has_touch) {
    out->max_touch_points_options = touch;
  }
  return true;
}

bool ParseUserAgentData(const base::DictValue& dict,
                        UserAgentDataConfig* out,
                        std::string* error) {
  return ReadBrandList(dict, "brands", &out->brands, error) &&
         ReadBrandList(dict, "full_version_list", &out->full_version_list,
                       error) &&
         ReadBool(dict, "mobile", &out->mobile, error) &&
         ReadString(dict, "platform", &out->platform, error) &&
         ReadString(dict, "platform_version", &out->platform_version, error) &&
         ReadString(dict, "architecture", &out->architecture, error) &&
         ReadString(dict, "bitness", &out->bitness, error) &&
         ReadString(dict, "model", &out->model, error) &&
         ReadBool(dict, "wow64", &out->wow64, error) &&
         ReadString(dict, "ua_full_version", &out->ua_full_version, error);
}

bool ParsePermissions(const base::DictValue& dict,
                      PermissionsConfig* out,
                      std::string* error) {
  return ReadStringMap(dict, "policies", &out->policies, error);
}

bool ParseTimezone(const base::DictValue& dict,
                   TimezoneConfig* out,
                   std::string* error) {
  return ReadString(dict, "iana_name", &out->iana_name, error) &&
         ReadInt(dict, "offset_minutes", &out->offset_minutes, error) &&
         ReadBool(dict, "dst_active", &out->dst_active, error) &&
         ReadString(dict, "abbreviation", &out->abbreviation, error);
}

bool ParseLocale(const base::DictValue& dict,
                 LocaleConfig* out,
                 std::string* error) {
  return ReadString(dict, "primary", &out->primary, error) &&
         ReadString(dict, "intl_locale", &out->intl_locale, error) &&
         ReadString(dict, "numbering_system", &out->numbering_system, error) &&
         ReadString(dict, "calendar", &out->calendar, error) &&
         ReadString(dict, "hour_cycle", &out->hour_cycle, error) &&
         ReadString(dict, "currency", &out->currency, error);
}

bool ParseGeolocation(const base::DictValue& dict,
                      GeolocationConfig* out,
                      std::string* error) {
  if (!ReadString(dict, "mode", &out->mode, error)) {
    return false;
  }
  auto read_optional = [&](const char* key, std::optional<double>* target) {
    const base::Value* value = dict.Find(key);
    if (!value || value->is_none()) {
      target->reset();
      return true;
    }
    if (!value->is_double() && !value->is_int()) {
      *error = std::string("non-numeric field: ") + key;
      return false;
    }
    *target = value->GetDouble();
    return true;
  };
  return read_optional("latitude", &out->latitude) &&
         read_optional("longitude", &out->longitude) &&
         read_optional("accuracy", &out->accuracy) &&
         read_optional("altitude", &out->altitude) &&
         read_optional("altitude_accuracy", &out->altitude_accuracy) &&
         read_optional("heading", &out->heading) &&
         read_optional("speed", &out->speed) &&
         ReadInt(dict, "timestamp_jitter_ms", &out->timestamp_jitter_ms, error);
}

bool ParseProxy(const base::DictValue& dict, ProxyConfig* out, std::string* error) {
  return ReadBool(dict, "enabled", &out->enabled, error) &&
         ReadString(dict, "protocol", &out->protocol, error) &&
         ReadString(dict, "host", &out->host, error) &&
         ReadInt(dict, "port", &out->port, error) &&
         ReadString(dict, "username", &out->username, error) &&
         ReadString(dict, "password", &out->password, error) &&
         ReadString(dict, "auth_method", &out->auth_method, error) &&
         ReadInt(dict, "connect_timeout_ms", &out->connect_timeout_ms, error) &&
         ReadInt(dict, "read_timeout_ms", &out->read_timeout_ms, error);
}

bool ParseWebRtc(const base::DictValue& dict,
                 WebRtcConfig* out,
                 std::string* error) {
  return ReadString(dict, "mode", &out->mode, error) &&
         ReadString(dict, "spoofed_public_ip_v4", &out->spoofed_public_ip_v4,
                    error) &&
         ReadString(dict, "spoofed_public_ip_v6", &out->spoofed_public_ip_v6,
                    error) &&
         ReadString(dict, "spoofed_local_ip_v4", &out->spoofed_local_ip_v4,
                    error) &&
         ReadBool(dict, "mdns_enabled", &out->mdns_enabled, error) &&
         ReadBool(dict, "block_non_proxied_udp", &out->block_non_proxied_udp,
                  error) &&
         ReadString(dict, "ice_transport_policy", &out->ice_transport_policy,
                    error);
}

bool ParseDns(const base::DictValue& dict, DnsConfig* out, std::string* error) {
  return ReadString(dict, "mode", &out->mode, error) &&
         ReadStringList(dict, "servers", &out->servers, error) &&
         ReadString(dict, "doh_url", &out->doh_url, error) &&
         ReadString(dict, "dot_host", &out->dot_host, error) &&
         ReadBool(dict, "prevent_dns_leak", &out->prevent_dns_leak, error);
}

bool ParseTls(const base::DictValue& dict, TlsConfig* out, std::string* error) {
  return ReadString(dict, "mode", &out->mode, error) &&
         ReadStringList(dict, "cipher_suites", &out->cipher_suites, error) &&
         ReadStringList(dict, "supported_groups", &out->supported_groups,
                        error) &&
         ReadStringList(dict, "signature_algorithms",
                        &out->signature_algorithms, error) &&
         ReadStringList(dict, "alpn_protocols", &out->alpn_protocols, error) &&
         ReadStringList(dict, "versions", &out->versions, error) &&
         ReadStringList(dict, "extension_order", &out->extension_order, error) &&
         ReadString(dict, "salt", &out->salt, error) &&
         ReadBool(dict, "grease_enabled", &out->grease_enabled, error);
}

bool ParseHttpHeaders(const base::DictValue& dict,
                      HttpHeadersConfig* out,
                      std::string* error) {
  return ReadString(dict, "accept_language", &out->accept_language, error) &&
         ReadString(dict, "dnt", &out->dnt, error) &&
         ReadString(dict, "sec_ch_ua", &out->sec_ch_ua, error) &&
         ReadString(dict, "sec_ch_ua_mobile", &out->sec_ch_ua_mobile, error) &&
         ReadString(dict, "sec_ch_ua_platform", &out->sec_ch_ua_platform,
                    error) &&
         ReadString(dict, "sec_ch_ua_platform_version",
                    &out->sec_ch_ua_platform_version, error) &&
         ReadString(dict, "sec_ch_ua_arch", &out->sec_ch_ua_arch, error) &&
         ReadString(dict, "sec_ch_ua_bitness", &out->sec_ch_ua_bitness, error) &&
         ReadString(dict, "sec_ch_ua_model", &out->sec_ch_ua_model, error) &&
         ReadString(dict, "sec_ch_ua_full_version_list",
                    &out->sec_ch_ua_full_version_list, error) &&
         ReadStringMap(dict, "custom_headers", &out->custom_headers, error);
}

bool ParseGeoSync(const base::DictValue& dict,
                  GeoSyncConfig* out,
                  std::string* error) {
  return ReadBool(dict, "enabled", &out->enabled, error) &&
         ReadBool(dict, "lock_manual_overrides", &out->lock_manual_overrides,
                  error) &&
         ReadBool(dict, "sync_timezone", &out->sync_timezone, error) &&
         ReadBool(dict, "sync_locale", &out->sync_locale, error) &&
         ReadBool(dict, "sync_geolocation", &out->sync_geolocation, error) &&
         ReadBool(dict, "sync_webrtc_ip", &out->sync_webrtc_ip, error);
}

bool ParseGeoIntelligence(const base::DictValue& dict,
                          GeoIntelligenceConfig* out,
                          std::string* error) {
  return ReadString(dict, "exit_ip_v4", &out->exit_ip_v4, error) &&
         ReadString(dict, "exit_ip_v6", &out->exit_ip_v6, error) &&
         ReadString(dict, "country_code", &out->country_code, error) &&
         ReadString(dict, "country_name", &out->country_name, error) &&
         ReadString(dict, "region_code", &out->region_code, error) &&
         ReadString(dict, "region_name", &out->region_name, error) &&
         ReadString(dict, "city", &out->city, error) &&
         ReadString(dict, "postal_code", &out->postal_code, error) &&
         ReadInt(dict, "asn", &out->asn, error) &&
         ReadString(dict, "isp", &out->isp, error) &&
         ReadString(dict, "org", &out->org, error) &&
         ReadBool(dict, "is_mobile_carrier", &out->is_mobile_carrier, error) &&
         ReadBool(dict, "is_datacenter", &out->is_datacenter, error) &&
         ReadInt(dict, "latency_ms", &out->latency_ms, error);
}

}  // namespace

bool ParseProfileConfig(const std::string& json,
                        ProfileConfig* config,
                        std::string* error) {
  // Chromium 159's JSONReader is a set of static methods: it hands back
  // the parsed document in an optional rather than filling an out
  // parameter.  ``ReadDict`` is the right entry point here because the
  // root of the configuration is a JSON object, and it rejects a document
  // whose root is anything else.
  //
  // The options are the strict RFC 8259 ones: no trailing commas and no
  // comments.  A configuration that is not strict JSON is a configuration
  // that two implementations may read differently.
  std::optional<base::DictValue> root =
      base::JSONReader::ReadDict(json, base::JSON_PARSE_RFC);
  if (!root) {
    *error = "the configuration is not a valid JSON object";
    return false;
  }
  const base::DictValue& dict = *root;

  ProfileConfig parsed;

  // The identity block holds the profile id next to the seed config.
  const base::DictValue* identity = FindDict(dict, "identity", error);
  if (!identity) return false;
  if (!ReadString(*identity, "id", &parsed.profile_id, error)) {
    return false;
  }

  const base::DictValue* seed = FindDict(dict, "seed_config", error);
  if (!seed) return false;
  if (!ParseSeed(*seed, &parsed.seed_config, error)) return false;

  const base::DictValue* build = FindDict(dict, "android_build", error);
  if (!build) return false;
  if (!ParseAndroidBuild(*build, &parsed.android_build, error)) return false;

  const base::DictValue* abi = FindDict(dict, "android_abi_soc", error);
  if (!abi) return false;
  if (!ParseAndroidAbiSoc(*abi, &parsed.android_abi_soc, error)) return false;

  const base::DictValue* navigator = FindDict(dict, "navigator", error);
  if (!navigator) return false;
  if (!ParseNavigator(*navigator, &parsed.navigator, error)) return false;

  const base::DictValue* uad = FindDict(dict, "user_agent_data", error);
  if (!uad) return false;
  if (!ParseUserAgentData(*uad, &parsed.user_agent_data, error)) return false;

  const base::DictValue* permissions = FindDict(dict, "permissions", error);
  if (!permissions) return false;
  if (!ParsePermissions(*permissions, &parsed.permissions, error)) return false;

  const base::DictValue* screen = FindDict(dict, "screen", error);
  if (!screen) return false;
  if (!ParseScreen(*screen, &parsed.screen, error)) return false;

  const base::DictValue* orientation = FindDict(dict, "orientation", error);
  if (!orientation) return false;
  if (!ParseOrientation(*orientation, &parsed.orientation, error)) return false;

  const base::DictValue* viewport = FindDict(dict, "viewport", error);
  if (!viewport) return false;
  if (!ParseViewport(*viewport, &parsed.viewport, error)) return false;

  const base::DictValue* media = FindDict(dict, "media_queries", error);
  if (!media) return false;
  if (!ParseMediaQueries(*media, &parsed.media_queries, error)) return false;

  const base::DictValue* timezone = FindDict(dict, "timezone", error);
  if (!timezone) return false;
  if (!ParseTimezone(*timezone, &parsed.timezone, error)) return false;

  const base::DictValue* locale = FindDict(dict, "locale", error);
  if (!locale) return false;
  if (!ParseLocale(*locale, &parsed.locale, error)) return false;

  const base::DictValue* geolocation = FindDict(dict, "geolocation", error);
  if (!geolocation) return false;
  if (!ParseGeolocation(*geolocation, &parsed.geolocation, error)) return false;

  const base::DictValue* canvas = FindDict(dict, "canvas_2d", error);
  if (!canvas) return false;
  if (!ParseCanvas(*canvas, &parsed.canvas_2d, error)) return false;

  const base::DictValue* webgl = FindDict(dict, "webgl", error);
  if (!webgl) return false;
  if (!ParseWebGL(*webgl, &parsed.webgl, error)) return false;

  const base::DictValue* webgpu = FindDict(dict, "webgpu", error);
  if (!webgpu) return false;
  if (!ParseWebGPU(*webgpu, &parsed.webgpu, error)) return false;

  const base::DictValue* audio = FindDict(dict, "audio", error);
  if (!audio) return false;
  if (!ParseAudio(*audio, &parsed.audio, error)) return false;

  const base::DictValue* rects = FindDict(dict, "rects", error);
  if (!rects) return false;
  if (!ParseRects(*rects, &parsed.rects, error)) return false;

  const base::DictValue* fonts = FindDict(dict, "fonts", error);
  if (!fonts) return false;
  if (!ParseFonts(*fonts, &parsed.fonts, error)) return false;

  const base::DictValue* media_devices = FindDict(dict, "media_devices", error);
  if (!media_devices) return false;
  if (!ParseMediaDevices(*media_devices, &parsed.media_devices, error))
    return false;

  const base::DictValue* storage = FindDict(dict, "storage_network", error);
  if (!storage) return false;
  if (!ParseStorageNetwork(*storage, &parsed.storage_network, error))
    return false;

  const base::DictValue* battery = FindDict(dict, "battery_speech_gamepad", error);
  if (!battery) return false;
  if (!ParseBatterySpeechGamepad(*battery,
                                  &parsed.battery_speech_gamepad, error)) {
    return false;
  }

  const base::DictValue* sensors = FindDict(dict, "sensors", error);
  if (!sensors) return false;
  if (!ParseSensors(*sensors, &parsed.sensors, error)) return false;

  const base::DictValue* proxy = FindDict(dict, "proxy", error);
  if (!proxy) return false;
  if (!ParseProxy(*proxy, &parsed.proxy, error)) return false;

  const base::DictValue* geo_sync = FindDict(dict, "geo_sync", error);
  if (!geo_sync) return false;
  if (!ParseGeoSync(*geo_sync, &parsed.geo_sync, error)) return false;

  const base::DictValue* geo_intel = FindDict(dict, "geo_intelligence", error);
  if (!geo_intel) return false;
  if (!ParseGeoIntelligence(*geo_intel, &parsed.geo_intelligence, error))
    return false;

  const base::DictValue* webrtc = FindDict(dict, "webrtc", error);
  if (!webrtc) return false;
  if (!ParseWebRtc(*webrtc, &parsed.webrtc, error)) return false;

  const base::DictValue* dns = FindDict(dict, "dns", error);
  if (!dns) return false;
  if (!ParseDns(*dns, &parsed.dns, error)) return false;

  const base::DictValue* tls = FindDict(dict, "tls", error);
  if (!tls) return false;
  if (!ParseTls(*tls, &parsed.tls, error)) return false;

  const base::DictValue* headers = FindDict(dict, "http_headers", error);
  if (!headers) return false;
  if (!ParseHttpHeaders(*headers, &parsed.http_headers, error))
    return false;

  *config = std::move(parsed);
  return true;
}

bool LoadProfileConfigFromCommandLine(ProfileConfig* config, std::string* error) {
  const base::CommandLine* command_line =
      base::CommandLine::ForCurrentProcess();

  // The configuration is handed to the renderer as the document itself,
  // not as a path.  The renderer is sandboxed and cannot open a file in
  // the user data directory, so a path would reach a process that cannot
  // use it -- and a process that cannot read the profile reports the
  // host's values, which is exactly what a site is looking for.
  //
  // The browser process, which is not sandboxed, reads the file and
  // puts the document on the command line of the renderer.
  //
  // The document is base64 encoded before it goes on the command line.
  // A Windows command line is a single string that the system splits
  // into arguments, and the quoting rules it uses cannot describe a
  // document that itself contains quotes, spaces and newlines: the
  // renderer would receive a fragment of the JSON, parse it, and fall
  // back to the host's values.  Base64 has no spaces, no quotes and no
  // newlines, so the argument survives the trip intact.
  const std::string inline_encoded =
      command_line->GetSwitchValueASCII(kConfigSourceSwitchName);
  if (!inline_encoded.empty()) {
    std::string decoded;
    if (!InflateFromCommandLine(inline_encoded, &decoded)) {
      *error = "the --" + std::string(kConfigSourceSwitchName) +
               " switch is not valid base64";
      return false;
    }
    return ParseProfileConfig(decoded, config, error);
  }

  const std::string path = command_line->GetSwitchValueASCII("nova-config");
  if (path.empty()) {
    *error = "the --nova-config switch is missing";
    return false;
  }

  // The path comes from the command line, so it is validated before it is
  // opened: it must be an absolute path to a regular file.
  const base::FilePath file_path(base::FilePath::FromUTF8Unsafe(path));
  if (!file_path.IsAbsolute()) {
    *error = "the --nova-config path must be absolute: " + path;
    return false;
  }

  std::string json;
  if (!base::ReadFileToStringWithMaxSize(file_path, &json, 8 * 1024 * 1024)) {
    // The reason matters: a file that is not there and a file that the
    // sandbox will not let the process open look the same from here, and
    // they have different fixes.
    //
    // NOTE: the diagnostics below must not touch the filesystem.  This
    // runs inside the renderer, on a thread that has been marked as
    // non-blocking; a stat() here trips the
    // "tls_blocking_disallowed" DCHECK and takes the whole browser down.
    // The error code that the failed read already left behind is enough.
    *error = "the configuration file could not be read: " + path +
             " (last error: " +
             logging::SystemErrorCodeToString(logging::GetLastSystemErrorCode()) +
             ")";
    return false;
  }

  return ParseProfileConfig(json, config, error);
}

const char kConfigSourceSwitchName[] = "nova-config-source";

// ---------------------------------------------------------------------------
// The browser process's copy of the profile
// ---------------------------------------------------------------------------
//
// The browser process is given a path and it is the only process that is
// not sandboxed, so it is the one that reads the file.  The startup code
// reads it and hands the document here.
//
// The document is handed over rather than read again because the read
// may only happen on the main thread during startup, and the hooks are
// reached from threads that are not allowed to wait on the disk.

namespace {

// The document.  Written once on the main thread during startup and only
// read afterwards, so a plain pointer swap is enough.
std::string* const g_browser_config_document = new std::string();

}  // namespace

void SetBrowserConfigDocument(const std::string& document) {
  *g_browser_config_document = document;
}

std::string ReadBrowserConfigDocument() {
  return *g_browser_config_document;
}

}  // namespace nova
