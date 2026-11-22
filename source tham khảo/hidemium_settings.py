"""Hidemium v5 profile settings: the option lists and the native mapping.

Every value here comes from the Hidemium v5 profile UI
(cache-ui/assets/defaultConfigConst-DfhyFfjS.js) and was checked against the
installed core (155.0.8059.40). The 248 `hidemium.*` keys were extracted from
chrome.dll, the command line switches from app.asar.

The core reads its configuration from `config.hidemium` (one `key:value` line
per setting, the line itself hex encoded) and `network.hidemium` (plain text,
the IP values as plain IP literals). This module only builds those values and the
command line; it never writes files.

What the probes against the core proved, and the raw Hidemium UI names hide:

* `hidemium.canvas.mode` only knows `noise-brave`, `noise-pro`, `noise-ab`
  and `off`. The mode `noise-e` makes the core hang and exit, and the modes
  `noise`, `nature` and `perfect` are ignored, so the page sees the canvas
  of the machine instead of the one of the profile.
* The canvas and the audio seed use the hex form the Hidemium app writes:
  `0.` followed by ten hexadecimal digits. The canvas stays unique at a
  million profiles; the audio is still quantized by the core into seven
  buckets.
* `navigator.platform` must be `Linux armv81`; the core reads the value
  verbatim, and the DevTools override has to send the same string, or the
  worker and the page disagree and the profile is refused.
* `hidemium.webgl.webgl_param.*` overrides the values a site reads with
  `getParameter()`, and `hidemium.value.battery.*` and
  `hidemium.network.*` are applied as given.
* The WebRTC mode only takes effect together with `network.hidemium`.
"""
import base64
import hashlib
import json
import random
import re
from pathlib import Path

from android_font_lists import ANDROID_FONT_BASE, ANDROID_FONT_VARYING

# ---------------------------------------------------------------- option lists
# The values and their order are the ones the Hidemium v5 UI offers.
CANVAS_MODES = ('noise-pro', 'noise', 'noise-e', 'perfect', 'off', 'noise-brave', 'personal-device')
CLIENT_RECTS_MODES = ('noise-2', 'noise', 'off')
WEBRTC_MODES = ('public', 'real', 'disabled', 'disable-stun')
# The WebRTC choice of the NOVA interface and the mode of the core.
WEBRTC_BY_PROFILE = {'proxy_only': 'public', 'none': 'disabled', 'default': 'real'}
MEDIA_MASKING = ('disable', 'noise', 'noise-device')
MEDIA_PERMISSIONS = ('default', 'allow', 'block')
NOISE_ON_OFF = ('noise', 'off')
CLIPBOARD_ACCESS = ('off', 'allow', 'block')
NOTIFICATION_PERMISSION = ('ask', 'allow', 'block')
PASSWORD_AUTOSAVE = ('ask', 'on', 'off')
DNS_MODES = ('secure', 'automatic', 'custom', 'off')
EFFECTIVE_TYPES = ('4g', '3g', '2g', 'slow-2g')
# The kinds of link the genuine Hidemium app writes into `network.type`.
# Measured against the 78 profiles it wrote: all eight of these appear.
# The connection of a phone. The five values of `navigator.connection`
# have to agree with each other, so they are drawn as one set and not one
# by one: a random kind next to a random speed gives a pair that does not
# exist. A phone is on `cellular` or on `wifi`; `ethernet`, `wimax`,
# `bluetooth` and `none` belong to a computer.
#
# The limits are the ones Chrome uses to derive `effectiveType` from the
# measured speed: 4G from 10 Mbps and under 70 ms, 3G from 0.7 Mbps and
# under 700 ms, 2G from 0.075 Mbps, slow-2G below that.
#
# label -> (rtt min, rtt max, downlink floor)
NETWORK_LIMITS = {
    '4g': (0, 69, 10.0),
    '3g': (70, 699, 0.7),
    '2g': (700, 899, 0.075),
    'slow-2g': (900, 999, 0.0001),
}
FONT_MODES = ('nature', 'noise', 'off')
STORAGE_MODES = ('real', 'noise')
# The range of the storage of a phone, in bytes. Measured against the 48
# profiles the genuine Hidemium app wrote: 83751862272 to 1061930663936, and
# the values are not round numbers, so the app draws the bytes at random
# inside that range. A round size (80 GB, 100 GB) would give only a few
# hundred values for a million profiles.
STORAGE_SIZE_MIN = 83751862272
STORAGE_SIZE_MAX = 1061930663936
GEOLOCATION_BEHAVIOURS = ('prompt', 'allow', 'block')
POPUP_MODES = ('block', 'allow')

# ------------------------------------------------------------------ shapes
# The real Hidemium app does not write the same set of keys in every profile:
# it copies a whole template. Measured against the 341 profiles it wrote, the
# profiles of the app fall into these shapes:
#
#   identity only   34.8%  the device keys and nothing else
#   + canvas        19.7%  the canvas seed
#   + audio         15.8%  the audio seed
#   + both          10.6%  the canvas and the audio seeds
#   + webrtc         6.5%
#   + rects          4.7%
#   + webgl image    2.9%
#   + media mask     1.8%
#   + everything     4.7%  the full bundle of 23 optional keys
#
# NOVA used to write the full bundle in every profile, so every NOVA profile
# looked like the 4.7% group and a site could group the whole installation
# with one request. A profile now picks its shape from the seed.
#
# The names are the optional key groups. The keys that identify the device
# are always written and are not part of a shape.
SHAPES = ('plain', 'canvas', 'audio', 'canvas+audio', 'webrtc', 'rects',
          'webgl-image', 'media', 'net', 'full')
# The chance of every shape, in the order of `SHAPES`. The sum is one.
SHAPE_WEIGHTS = (0.348, 0.197, 0.158, 0.106, 0.065, 0.047, 0.029, 0.018, 0.020, 0.012)
# The optional key groups of every shape. A group names the keys the shape
# writes; the keys of a group that the shape does not have are left out.
SHAPE_GROUPS = {
    'plain': (),
    'canvas': ('canvas',),
    'audio': ('audio',),
    'canvas+audio': ('canvas', 'audio'),
    'webrtc': ('webrtc',),
    'rects': ('rects',),
    'webgl-image': ('webgl-image',),
    'media': ('media',),
    'net': ('net', 'battery', 'geo', 'bluetooth', 'ports'),
    'full': ('canvas', 'audio', 'webrtc', 'rects', 'webgl-image', 'media',
             'net', 'battery', 'geo', 'privacy'),
}

# The canvas mode of the core for every mode the UI offers. The UI names are
# not the modes of the core, and the core ignores the unknown ones, so the
# name has to be translated before it is written.
CANVAS_CORE_MODE = {
    'noise-pro': 'noise-pro',
    'noise': 'noise-brave',
    'noise-e': 'noise-ab',
    'perfect': 'noise-brave',
    'off': 'off',
    'noise-brave': 'noise-brave',
    'personal-device': 'noise-brave',
}
# The platform string of the core for an Android arm64 device. The core reads
# the value verbatim, so the DevTools override has to send the same string,
# or the worker and the page disagree. The profiles captured from the
# Hidemium app all use this string.
ANDROID_PLATFORM = 'Linux armv81'
# The version of the bundled core. The real Hidemium app writes it in the
# UA keys, so a profile announces the same browser as the app.
CHROME_VERSION = '155.0.8059.40'

# The Chrome versions a profile may announce.
#
# Measured against the 48 profiles the genuine Hidemium app wrote: their
# `hidemium.navigator.useragent.version_useragent` holds 48 different builds
# spread over eleven majors, from 146 to 156. NOVA used to write the version
# of the bundled core in every profile, so every profile of the installation
# announced `Chrome/155` and a site grouped the whole fleet with one request.
#
# The list holds the exact builds of the genuine profiles, so a profile
# announces a Chrome that exists and the version of the page, the version of
# the UA-CH brands and the version of the worker always agree.
CHROME_VERSIONS = (
    '146.0.7680.31', '146.0.7680.58', '147.0.7681.1', '147.0.7684.0',
    '147.0.7703.1', '148.0.7731.0', '148.0.7734.2', '148.0.7752.0',
    '148.0.7778.0', '149.0.7796.1', '149.0.7797.1', '149.0.7803.1',
    '149.0.7827.150', '149.0.7827.2', '149.0.7827.3', '149.0.7827.42',
    '150.0.7871.125', '150.0.7871.184', '150.0.7871.27', '151.0.7889.0',
    '151.0.7899.1', '151.0.7907.1', '151.0.7922.132', '151.0.7922.52',
    '151.0.7922.55', '151.0.7922.81', '152.0.7933.1', '152.0.7933.2',
    '152.0.7940.2', '152.0.7973.1', '152.0.7977.121', '152.0.7977.43',
    '152.0.7977.57', '152.0.7977.87', '153.0.7980.0', '153.0.7991.3',
    '153.0.7992.3', '153.0.7994.0', '154.0.8037.39', '154.0.8037.61',
    '154.0.8037.92', '155.0.8046.0', '155.0.8053.2', '155.0.8059.38',
    '156.0.8061.2', '156.0.8066.0', '156.0.8066.8', '156.0.8073.3',
)

# ---------------------------------------------------------------- GPU family
# The WebGL limits of a phone are a property of the GPU family, not of the
# profile: two phones with the same GPU report the same limits. The table is
# a reference of the families the device catalog uses, not a random draw, so
# a profile never reports a limit its GPU cannot have.
#
# The values are the ones the WebGL 1 and 2 specifications fix plus the
# limits the mobile GPUs of the family are known to have. `gl_version` and
# `gl_version2` are the strings the driver reports.
#
# The `webgl_param` keys the core reads. Measured against the core: it holds
# 74 names in `chrome.dll`, but only these 46 of them change the value the
# page reads. The other 28 are in the binary but the core never applies them,
# so writing them is a no-op (measured one key at a time, with the sentinel
# read back from `getParameter`).
WEBGL_PARAM_KEYS = (
    'aliased_line_width_range', 'aliased_point_size_range', 'alpha_bits',
    'blue_bits', 'depth_bits', 'green_bits', 'red_bits', 'stencil_bits',
    'sample_buffers', 'subpixel_bits', 'max_3d_texture_size',
    'max_array_texture_layers', 'max_color_attachments',
    'max_combined_texture_image_units', 'max_combined_uniform_blocks',
    'max_cube_map_texture_size', 'max_draw_buffers', 'max_element_index',
    'max_fragment_input_components', 'max_fragment_uniform_blocks',
    'max_fragment_uniform_components', 'max_fragment_uniform_vectors',
    'max_program_texel_offset', 'max_renderbuffer_size', 'max_samples',
    'max_server_wait_timeout', 'max_texture_image_units', 'max_texture_lod_bias',
    'max_texture_size', 'max_transform_feedback_separate_components',
    'max_uniform_block_size', 'max_uniform_buffer_bindings',
    'max_varying_vectors', 'max_vertex_attribs', 'max_vertex_output_components',
    'max_vertex_texture_image_units', 'max_vertex_uniform_blocks',
    'max_vertex_uniform_components', 'max_vertex_uniform_vectors',
    'max_viewport_dims', 'min_program_texel_offset',
    'uniform_buffer_offset_alignment',
    'gl_version', 'gl_version2', 'shading_language_version',
    'shading_language_version2',
)

GPU_FAMILIES = {
    'adreno-540': {
        'gl_version': 'WebGL 1.0 (OpenGL ES 3.2 Chromium)', 'gl_version2': 'WebGL 2.0 (OpenGL ES 3.2 Chromium)',
        'shading_language_version': 'WebGL GLSL ES 3.00', 'shading_language_version2': 'WebGL GLSL ES 3.00',
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 24, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'adreno-610': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-618': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-630': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-640': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-660': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 24, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'adreno-730': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-732': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-740': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-750': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 24, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'adreno-760': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-810': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-830': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'adreno-840': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g52': {
        'gl_version': 'WebGL 1.0 (OpenGL ES 3.0 Chromium)', 'gl_version2': 'WebGL 2.0 (OpenGL ES 3.0 Chromium)',
        'shading_language_version': 'WebGL GLSL ES 1.00', 'shading_language_version2': 'WebGL GLSL ES 3.00',
        'max_texture_size': 8192, 'max_renderbuffer_size': 8192, 'max_cube_map_texture_size': 8192,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 16, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'mali-g57': {
        'max_texture_size': 8192, 'max_renderbuffer_size': 8192, 'max_cube_map_texture_size': 8192,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g610': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g615': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g68': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g70': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g71': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g710': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 16, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'mali-g715': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 16, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'mali-g720': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'mali-g925': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 16, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'immortalis-g715': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'immortalis-g720': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'immortalis-g925': {
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'powervr-ge8300': {
        'gl_version': 'WebGL 1.0 (OpenGL ES 3.0 Chromium)', 'gl_version2': 'WebGL 2.0 (OpenGL ES 3.0 Chromium)',
        'shading_language_version': 'WebGL GLSL ES 1.00', 'shading_language_version2': 'WebGL GLSL ES 3.00',
        'max_texture_size': 8192, 'max_renderbuffer_size': 8192, 'max_cube_map_texture_size': 8192,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 16, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
    'powervr-ge8320': {
        'max_texture_size': 8192, 'max_renderbuffer_size': 8192, 'max_cube_map_texture_size': 8192,
        'max_3d_texture_size': 2048, 'max_array_texture_layers': 2048, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_texture_max_anisotropy_ext': 16, 'max_texture_lod_bias': 8,
    },
    'xclipse-940': {
        'gl_version': 'WebGL 1.0 (OpenGL ES 3.2 Chromium)', 'gl_version2': 'WebGL 2.0 (OpenGL ES 3.2 Chromium)',
        'shading_language_version': 'WebGL GLSL ES 1.00', 'shading_language_version2': 'WebGL GLSL ES 3.00',
        'max_texture_size': 16384, 'max_renderbuffer_size': 16384, 'max_cube_map_texture_size': 16384,
        'max_3d_texture_size': 4096, 'max_array_texture_layers': 4096, 'max_vertex_attribs': 16,
        'max_combined_texture_image_units': 32, 'max_texture_image_units': 16,
        'max_vertex_texture_image_units': 16, 'max_draw_buffers': 8, 'max_color_attachments': 8,
        'max_samples': 4, 'sample_buffers': 1, 'max_elements_vertices': 65536, 'max_elements_indices': 65536,
        'max_element_index': 65536, 'max_varying_vectors': 15, 'max_varying_components': 60,
        'max_vertex_uniform_vectors': 256, 'max_fragment_uniform_vectors': 256,
        'max_vertex_uniform_components': 1024, 'max_fragment_uniform_components': 1024,
        'max_combined_vertex_uniform_components': 1024, 'max_combined_fragment_uniform_components': 1024,
        'max_vertex_uniform_blocks': 12, 'max_fragment_uniform_blocks': 12,
        'max_combined_uniform_blocks': 24, 'max_uniform_block_size': 16384,
        'max_uniform_buffer_bindings': 24, 'max_vertex_output_components': 64,
        'max_fragment_input_components': 64, 'max_texture_max_anisotropy_ext': 16,
        'max_texture_lod_bias': 8, 'subpixel_bits': 4, 'red_bits': 8, 'green_bits': 8, 'blue_bits': 8,
        'alpha_bits': 8, 'depth_bits': 24, 'stencil_bits': 8, 'uniform_buffer_offset_alignment': 256,
        'min_program_texel_offset': -8, 'max_program_texel_offset': 7,
        'max_transform_feedback_interleaved_components': 64,
        'max_transform_feedback_separate_attribs': 4, 'max_transform_feedback_separate_components': 4,
        'max_server_wait_timeout': 1024, 'max_client_wait_timeout_webgl': 0,
        'aliased_line_width_range': '1,1', 'aliased_point_size_range': '1,1024',
        'stencil_value_mask': '4294967295', 'stencil_writemask': '4294967295',
        'stencil_back_value_mask': '4294967295', 'stencil_back_writemask': '4294967295',
        'webgl_extension': 'ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,'
                           'EXT_disjoint_timer_query,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,'
                           'EXT_texture_compression_bptc,EXT_texture_filter_anisotropic,OES_element_index_uint,'
                           'OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_half_float,'
                           'WEBGL_color_buffer_float,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_lose_context',
        'vertex_shader_high_float': 1, 'vertex_shader_medium_float': 1, 'vertex_shader_low_float': 1,
        'vertex_shader_high_int': 1, 'vertex_shader_medium_int': 1, 'vertex_shader_low_int': 1,
        'fragment_shader_high_float': 1, 'fragment_shader_medium_float': 1, 'fragment_shader_low_float': 1,
        'fragment_shader_high_int': 1, 'fragment_shader_medium_int': 1, 'fragment_shader_low_int': 1,
    },
}
# The GPU family of a renderer of the device catalog. The catalog lists the
# renderer as the manufacturer writes it ("Adreno (TM) 750", "Mali-G715 MP2"),
# so the family is the name of the GPU without the suffix.
GPU_FAMILY_BY_RENDERER = {
    'adreno-540': ('540',), 'adreno-610': ('610',), 'adreno-618': ('618',), 'adreno-630': ('630',),
    'adreno-640': ('640',), 'adreno-660': ('660',), 'adreno-730': ('730',), 'adreno-732': ('732',),
    'adreno-740': ('740',), 'adreno-750': ('750',), 'adreno-760': ('760',), 'adreno-810': ('810',),
    'adreno-830': ('830',), 'adreno-840': ('840',),
    'mali-g52': ('Mali-G52',), 'mali-g57': ('Mali-G57',), 'mali-g610': ('Mali-G610',),
    'mali-g615': ('Mali-G615',), 'mali-g68': ('Mali-G68',), 'mali-g70': ('Mali-G70',),
    'mali-g71': ('Mali-G71',), 'mali-g710': ('Mali-G710',), 'mali-g715': ('Mali-G715',),
    'mali-g720': ('Mali-G720',), 'mali-g925': ('Mali-G925',),
    'immortalis-g715': ('Immortalis-G715',), 'immortalis-g720': ('Immortalis-G720',),
    'immortalis-g925': ('Immortalis-G925',),
    'powervr-ge8300': ('GE8300',), 'powervr-ge8320': ('GE8320',),
    'xclipse-940': ('Xclipse 940',),
}
# The family of a renderer that is not in the table above: the closest one
# from the same manufacturer. A phone always has a GPU of one of these
# families; the fallback only exists for a catalog entry of an unknown GPU.
GPU_FAMILY_FALLBACK = {
    'adreno': 'adreno-640', 'mali': 'mali-g710', 'immortalis': 'immortalis-g715',
    'powervr': 'powervr-ge8300', 'xclipse': 'xclipse-940',
}

# The WebGPU limits of the family. The mobile GPUs of the table above have no
# public WebGPU implementation, so the limits are the ones the Chrome
# implementation on a mobile GPU of the same class reports.
WEBGPU_FAMILIES = {
    'adreno': {
        'vendor': 'qualcomm', 'architecture': 'Adreno',
        'features': 'depth-clip-control,depth32-filter-stencil8,indirect-first-instance,shader-f16,'
                    'timestamp-query,texture-compression-bc,texture-compression-etc2,texture-format-rg11b10ufloat-renderable',
        'limits': {
            'maxBindGroups': 4, 'maxBindGroupsPlusVertexBuffers': 24, 'maxBindingsPerBindGroup': 1000,
            'maxBufferSize': 268435456, 'maxColorAttachmentBytesPerSample': 32, 'maxColorAttachments': 8,
            'maxComputeInvocationsPerWorkgroup': 128, 'maxComputeWorkgroupSizeX': 128,
            'maxComputeWorkgroupSizeY': 128, 'maxComputeWorkgroupSizeZ': 64,
            'maxComputeWorkgroupStorageSize': 16384, 'maxComputeWorkgroupsPerDimension': 65535,
            'maxDynamicStorageBuffersPerPipelineLayout': 4, 'maxDynamicUniformBuffersPerPipelineLayout': 8,
            'maxImmediateSize': 0, 'maxInterStageShaderVariables': 16, 'maxSampledTexturesPerShaderStage': 16,
            'maxSamplersPerShaderStage': 16, 'maxStorageBufferBindingSize': 134217728,
            'maxStorageBuffersInFragmentStage': 8, 'maxStorageBuffersInVertexStage': 8,
            'maxStorageBuffersPerShaderStage': 8, 'maxStorageTexturesInFragmentStage': 4,
            'maxStorageTexturesInVertexStage': 4, 'maxStorageTexturesPerShaderStage': 4,
            'maxTextureArrayLayers': 2048, 'maxTextureDimension1D': 8192, 'maxTextureDimension2D': 8192,
            'maxTextureDimension3D': 2048, 'maxUniformBufferBindingSize': 65536,
            'maxUniformBuffersPerShaderStage': 12, 'maxVertexAttributes': 16,
            'maxVertexBufferArrayStride': 2048, 'maxVertexBuffers': 8,
            'minStorageBufferOffsetAlignment': 256, 'minUniformBufferOffsetAlignment': 256,
        },
    },
    'arm': {
        'vendor': 'arm', 'architecture': 'Mali',
        'features': 'depth-clip-control,depth32-filter-stencil8,indirect-first-instance,shader-f16,'
                    'timestamp-query,texture-compression-bc,texture-compression-etc2',
        'limits': {
            'maxBindGroups': 4, 'maxBindGroupsPlusVertexBuffers': 24, 'maxBindingsPerBindGroup': 1000,
            'maxBufferSize': 268435456, 'maxColorAttachmentBytesPerSample': 32, 'maxColorAttachments': 8,
            'maxComputeInvocationsPerWorkgroup': 128, 'maxComputeWorkgroupSizeX': 128,
            'maxComputeWorkgroupSizeY': 128, 'maxComputeWorkgroupSizeZ': 64,
            'maxComputeWorkgroupStorageSize': 16384, 'maxComputeWorkgroupsPerDimension': 65535,
            'maxDynamicStorageBuffersPerPipelineLayout': 4, 'maxDynamicUniformBuffersPerPipelineLayout': 8,
            'maxImmediateSize': 0, 'maxInterStageShaderVariables': 16, 'maxSampledTexturesPerShaderStage': 16,
            'maxSamplersPerShaderStage': 16, 'maxStorageBufferBindingSize': 134217728,
            'maxStorageBuffersInFragmentStage': 8, 'maxStorageBuffersInVertexStage': 8,
            'maxStorageBuffersPerShaderStage': 8, 'maxStorageTexturesInFragmentStage': 4,
            'maxStorageTexturesInVertexStage': 4, 'maxStorageTexturesPerShaderStage': 4,
            'maxTextureArrayLayers': 2048, 'maxTextureDimension1D': 8192, 'maxTextureDimension2D': 8192,
            'maxTextureDimension3D': 2048, 'maxUniformBufferBindingSize': 65536,
            'maxUniformBuffersPerShaderStage': 12, 'maxVertexAttributes': 16,
            'maxVertexBufferArrayStride': 2048, 'maxVertexBuffers': 8,
            'minStorageBufferOffsetAlignment': 256, 'minUniformBufferOffsetAlignment': 256,
        },
    },
    'imagination': {
        'vendor': 'imagination', 'architecture': 'PowerVR',
        'features': 'depth-clip-control,depth32-filter-stencil8,indirect-first-instance,shader-f16,'
                    'timestamp-query,texture-compression-bc,texture-compression-etc2',
        'limits': {
            'maxBindGroups': 4, 'maxBindGroupsPlusVertexBuffers': 24, 'maxBindingsPerBindGroup': 1000,
            'maxBufferSize': 268435456, 'maxColorAttachmentBytesPerSample': 32, 'maxColorAttachments': 8,
            'maxComputeInvocationsPerWorkgroup': 128, 'maxComputeWorkgroupSizeX': 128,
            'maxComputeWorkgroupSizeY': 128, 'maxComputeWorkgroupSizeZ': 64,
            'maxComputeWorkgroupStorageSize': 16384, 'maxComputeWorkgroupsPerDimension': 65535,
            'maxDynamicStorageBuffersPerPipelineLayout': 4, 'maxDynamicUniformBuffersPerPipelineLayout': 8,
            'maxImmediateSize': 0, 'maxInterStageShaderVariables': 16, 'maxSampledTexturesPerShaderStage': 16,
            'maxSamplersPerShaderStage': 16, 'maxStorageBufferBindingSize': 134217728,
            'maxStorageBuffersInFragmentStage': 8, 'maxStorageBuffersInVertexStage': 8,
            'maxStorageBuffersPerShaderStage': 8, 'maxStorageTexturesInFragmentStage': 4,
            'maxStorageTexturesInVertexStage': 4, 'maxStorageTexturesPerShaderStage': 4,
            'maxTextureArrayLayers': 2048, 'maxTextureDimension1D': 8192, 'maxTextureDimension2D': 8192,
            'maxTextureDimension3D': 2048, 'maxUniformBufferBindingSize': 65536,
            'maxUniformBuffersPerShaderStage': 12, 'maxVertexAttributes': 16,
            'maxVertexBufferArrayStride': 2048, 'maxVertexBuffers': 8,
            'minStorageBufferOffsetAlignment': 256, 'minUniformBufferOffsetAlignment': 256,
        },
    },
}

# The fonts an Android phone has. The list is the one of the Android system
# fonts, so a site that measures the text sees the metrics of an Android
# phone and not the fonts of the Windows machine.
ANDROID_FONT_CORE = ('Roboto', 'Noto Sans', 'Droid Sans', 'Noto Serif', 'Noto Color Emoji')
# The face the genuine app writes. It is the same in every profile.
ANDROID_FONTFACE = 'Dancing Script,Droid Sans Mono,Roboto'

# The fonts a phone has when the manufacturer adds them. A Samsung has the
# Samsung fonts, a Xiaomi the Miui ones.
ANDROID_FONT_EXTRA = {
    'Samsung': ('SamsungOne', 'SamsungSharpSans', 'Samsung Sans', 'One UI Sans'),
    'Xiaomi': ('MiSans', 'MiuiSans', 'MiproSans', 'Mi Latin', 'Mi Emoji'),
    'Huawei': ('HarmonyOS Sans', 'Huawei Sans', 'HwSans', 'HarmonyOS Sans SC'),
    'OPPO': ('OPPOSans', 'OPPO Sans 4.0', 'Oppo Sans'),
    'vivo': ('OriginSans', 'Vivo Sans', 'Vivo Theme'),
    'OnePlus': ('OPPOSans', 'OnePlus Sans', 'Noto Sans'),
    'realme': ('realme Sans', 'OPPOSans'),
    'Google': ('Google Sans', 'Product Sans', 'Roboto Slab'),
    'Motorola': ('Moto Sans', 'Motorola Sans'),
    'Nothing': ('Nothing Sans', 'Ndot', 'NType'),
    'Honor': ('Honor Sans', 'MagicOS Sans'),
}
# The Android fonts a phone can have that are not the core ones. The core
# reads a font list from the config, so the list decides what a site sees.
ANDROID_FONT_VARIANTS = (
    'Cutive Mono', 'Coming Soon', 'Dancing Script', 'Carrois Gothic', 'Cutive',
    'Nanum Gothic', 'Tinos', 'Ubuntu', 'Noto Sans Mono', 'Droid Sans Mono',
    'Noto Color Emoji', 'Noto Sans CJK', 'Droid Serif', 'Droid Sans Fallback',
)

# The camera names of the Android camera HAL. A phone reports one camera in
# the media list, so only the front name is used; the label follows the brand
# because the OEM names differ.
BRAND_CAMERAS = {
    'Samsung': ('camera2 0',), 'Google': ('camera2 0',), 'Xiaomi': ('camera2 0',),
    'OnePlus': ('camera2 0',), 'OPPO': ('camera2 0',), 'vivo': ('camera2 0',),
    'realme': ('camera2 0',), 'Huawei': ('camera2 0',), 'Honor': ('camera2 0',),
    'Motorola': ('camera2 0',), 'Nokia': ('camera2 0',), 'Sony': ('camera2 0',),
    'LG': ('camera2 0',), 'ZTE': ('camera2 0',), 'TECNO': ('camera2 0',),
    'Infinix': ('camera2 0',), 'Asus': ('camera2 0',),
}
DEFAULT_CAMERAS = ('camera2 0',)
BRAND_CAMERA_LABELS = {
    'Samsung': ('Front Camera',), 'Google': ('Front Camera',),
    'Xiaomi': ('Front Camera',), 'OnePlus': ('Front Camera',),
    'OPPO': ('Front Camera',), 'vivo': ('Front Camera',),
    'realme': ('Front Camera',), 'Huawei': ('Front Camera',),
    'Honor': ('Front Camera',), 'Asus': ('Front Camera',),
    'Motorola': ('Front Camera',),
}
MIC_LABEL = 'mic'
SPEAKER_LABEL = 'Speaker'
# The referers a profile comes from. The traffic of a profile that is used
# for a search engine or a social network comes from there.
REFERRERS = (
    'https://www.google.com/', 'https://www.google.com.vn/', 'https://www.bing.com/',
    'https://duckduckgo.com/', 'https://search.brave.com/', 'https://www.facebook.com/',
    'https://t.co/', 'https://l.facebook.com/', 'https://www.youtube.com/',
    'https://m.facebook.com/', 'https://www.tiktok.com/', '',
)
# The voices of the speech engine of an Android phone. The list is the one
# of the Google text to speech voices of Android, in the English language.
VOICE_LISTS = (
    ('Google US English', 'Google UK English Female', 'Google UK English Male'),
    ('Google UK English Female', 'Google US English', 'Google Español (Mexico)'),
    ('Google Español (Mexico)', 'Google US English', 'Google Français (France)'),
    ('Google Français (France)', 'Google Deutsch', 'Google US English'),
    ('Google Deutsch', 'Google 日本語', 'Google US English'),
    ('Google 日本語', 'Google 한국어', 'Google US English'),
    ('Google 한국어', 'Google US English', 'Google 中文（简体）'),
    ('Google 中文（简体）', 'Google US English', 'Google español (España)'),
)


def gpu_family(renderer):
    """The GPU family of a renderer of the device catalog.

    The catalog writes the renderer as the manufacturer writes it, so the
    family is looked up by the name of the GPU without the suffix. A
    renderer that is not in the table falls back to the closest family of
    the same manufacturer, so the limits are never random.
    """
    text = str(renderer or '')
    for family, names in GPU_FAMILY_BY_RENDERER.items():
        if any(name in text for name in names):
            return family
    for prefix, family in GPU_FAMILY_FALLBACK.items():
        if prefix.lower() in text.lower():
            return family
    return 'adreno-640'


def gpu_limits(renderer):
    """The WebGL limits of a renderer, merged with the values of the family."""
    limits = dict(GPU_FAMILIES['adreno-540'])
    limits.update(GPU_FAMILIES[gpu_family(renderer)])
    # The two names the core uses for the same limit and the sample count
    # of the WebGL 1 context.
    limits['gl_sample'] = limits['max_samples']
    limits['max_combined_texture_image_units2'] = limits['max_combined_texture_image_units']
    # The viewport limit is the same on every GPU of the catalog.
    limits.setdefault('max_viewport_dims', '16384,16384')
    return limits


def webgpu_family(renderer):
    """The WebGPU family of a renderer."""
    text = str(renderer or '').lower()
    if 'adreno' in text or 'qualcomm' in text:
        return 'adreno'
    if 'mali' in text or 'immortalis' in text:
        return 'arm'
    if 'powervr' in text or 'imagination' in text:
        return 'imagination'
    return 'arm'


def android_fonts(brand, seed):
    """The font list of an Android phone of a brand.

    The core has a built-in font table, so a family that is not in it falls
    back to the same face and the text measurement of the site shows the
    same width. The list therefore holds the families the core knows.
    """
    fonts = list(ANDROID_FONT_CORE)
    fonts.extend(ANDROID_FONT_EXTRA.get(brand, ()))
    # Two of the variant fonts, drawn from the seed, so two profiles of the
    # same model do not report the same font set.
    variants = seed_pick(seed, 'fonts', ANDROID_FONT_VARIANTS)
    extra = seed_pick(seed, 'fonts-extra', ANDROID_FONT_VARIANTS)
    for name in (variants, extra):
        if name and name not in fonts:
            fonts.append(name)
    return [name for name in fonts if name]



def default_settings():
    """The Hidemium v5 defaults, in the order the UI shows them."""
    return {
        'canvas': 'noise-brave',
        'canvas_rgb': True,
        'audio': 'noise',
        'client_rects': 'off',
        'webrtc': 'public',
        'webrtc_public_ip': '',
        'webrtc_local_ip': '',
        'media_masking': 'noise',
        'mic_permission': 'default',
        'camera_permission': 'default',
        'media_devices': {},
        'webgl_image': 'noise',
        'webgl_metadata': 'noise',
        'webgl_parameter': 'noise',
        'webgl_params': {},
        'webgpu': 'noise',
        'webgpu_params': {},
        'codecs': 'noise',
        'ssl': 'noise',
        'plugins': 'noise',
        'fonts': 'nature',
        # Without a list the core reports the fonts of the Windows machine
        # (Segoe UI, Calibri), which an Android profile cannot have.
        # This is one of the exact font lists measured in the real app.
        'fonts_list': 'Roboto,Noto Sans,Droid Sans,Noto Serif,Noto Sans Display,Samsung Sans',
        'battery': 'noise',
        'bluetooth': 'off',
        'do_not_track': 'on',
        # The core's "secure" mode leaves the name resolution without an
        # answer on this machine, so the page never loads. "automatic" keeps
        # the system resolver and works.
        'dns': 'automatic',
        'dns_domain': '',
        'akamai_bypass_dns': False,
        'ports_protect': '3389,5900,5800,7070,6568,5938',
        'disable_image': False,
        'disable_css_animation': False,
        'disable_autofill_popup': False,
        'restore_session': True,
        'hide_profile_name': True,
        'clipboard_access': 'off',
        'website_notification': 'ask',
        'password_autosave': 'ask',
        'cookie_enable': True,
        'popup_mode': 'block',
        # Empty means "let the profile derive it from the speed it drew".
        # A fixed label here would write the same kind of link in every
        # profile of the installation, and one request would group them.
        'effective_type': '',
        'network_type': 'wifi',
        # 0 means "derive from the effective type of the connection": a 4G
        # profile reports a fast link, a 2G profile a slow one. A number
        # here pins the value for every profile.
        'downlink': 0,
        'rtt': 0,
        'physical_memory': 0,
        'computer_name': '',
        'mac_address': '',
        'machine_id': '',
        'serial_number': '',
        'referer': '',
        'geolocation_behaviour': 'prompt',
        # Measured against the core: with the mode `real` the page reads the
        # quota of the Windows host, 10737418240, in every profile, so one
        # request groups every profile of the installation. The genuine
        # Hidemium app writes the mode `on` in all 48 of its profiles.
        'storage_mode': 'noise',
        'storage_size': 0,
        # None means "derive from the effective type": a 2G profile saves the
        # data, a 4G profile does not.
        'save_data': None,
        # The switches of the Hidemium v5 launcher.
        'disable_window_open': False,
        'enable_memory_saving_mode': False,
        'disable_ai_mode': False,
        'disable_avatar': True,
        'incognito': False,
        'disable_paskey': False,
        'enable_translation': False,
        'enable_accept_extension': False,
        'disable_window_hello': False,
        'always_allow_popups': False,
        'disable_app_launch_from_websites': False,
        'disable_machine_id': False,
        'enable_video_stream_spoofing': False,
        'enable_webgl2': True,
        'enable_swiftshader': False,
        'custom_launch_parameters': '',
    }


def seed_float(seed, salt='', digits=6):
    """A stable float in [0,1) from the profile seed.

    The core parses the canvas and the audio seed as a float. An integer
    seed makes it fall back to the real value, so the noise never applies.

    `digits` is the number of decimals. The canvas needs the full float64:
    with 6 decimals there are only 1,000,000 different values, so a million
    profiles must collide. The audio only has seven buckets in the core, so
    it keeps the short form.
    """
    digest = hashlib.sha256((str(seed) + salt).encode('utf8')).digest()
    value = int.from_bytes(digest[:8], 'big') / 0xFFFFFFFFFFFFFFFF
    return round(value, digits) if digits else value


def seed_pick(seed, salt, items):
    """A stable choice of `items` from the profile seed."""
    if not items:
        return None
    digest = hashlib.sha256((str(seed) + salt).encode('utf8')).digest()
    return items[int.from_bytes(digest[:4], 'big') % len(items)]


def seed_int(seed, salt, low, high):
    """A stable integer in [low,high] from the profile seed."""
    digest = hashlib.sha256((str(seed) + salt).encode('utf8')).digest()
    return low + int.from_bytes(digest[:4], 'big') % (high - low + 1)


def profile_shape(seed):
    """The shape of the profile: which optional key groups it carries.

    The real Hidemium app copies a whole template into a profile, so its
    profiles fall into a handful of shapes. NOVA writes the full bundle in
    every profile, which makes every NOVA profile look alike to a site. The
    shape is picked from the seed, so a million profiles spread over the
    shapes the same way the real app does.
    """
    point = seed_float(seed, 'shape', 6) * sum(SHAPE_WEIGHTS)
    upto = 0.0
    for shape, weight in zip(SHAPES, SHAPE_WEIGHTS):
        upto += weight
        if point < upto:
            return shape
    return SHAPES[-1]


def shape_groups(seed):
    """The optional key groups of the profile.

    The canvas, the audio, the WebRTC and the connection are in every
    shape. Measured against the core: a profile without
    `hidemium.canvas.value` paints with the seed of the host, so every such
    profile reports the same canvas hash. The seed is what makes each
    profile a different device, which is the goal of NOVA.

    The connection belongs to every shape too. The genuine app writes it in
    every profile, and a profile without the keys reads the connection of
    the Windows host, so a whole installation shares one round trip time.
    """
    return tuple(SHAPE_GROUPS[profile_shape(seed)]) + ('canvas', 'audio', 'webrtc', 'net')


def seed_hex(seed, salt, length):
    """A stable lowercase hex string of `length` characters."""
    digest = hashlib.sha256((str(seed) + salt).encode('utf8')).digest()
    return digest.hex()[:length]


def seed_hex_value(seed, salt='', digits=10):
    """A stable Hidemium canvas/audio seed in the app's native hex form.

    The real Hidemium app writes `0.` followed by hexadecimal digits, and the
    core accepts at least eight digits. Ten digits provide 16^10 values,
    enough to keep a million profiles apart.
    """
    return '0.' + seed_hex(seed, salt, digits)


def webgl_image_value(seed, renderer='', nudge=0, digits=17):
    """The native image noise value of the WebGL, as a decimal.

    Measured against the core: the value is parsed as a DECIMAL number, so
    the hex seed of the canvas is read as zero and the pixels of the host
    stay. The renderer is part of the hash, so two profiles of the fleet on
    different GPU rows never share a value, even when their seed is the
    same.

    `nudge` moves the deep columns of the decimal. The core keeps every
    digit (measured: a change in the seventeenth column moves the draw
    hash), so the guard against collisions can separate two profiles that
    landed on the same draw hash.
    """
    digest = hashlib.sha256(
        ('%s|%s|webgl-image' % (seed, renderer)).encode('utf8')).digest()
    value = int.from_bytes(digest[:8], 'big') % (10 ** digits)
    if nudge:
        coarse, deep = divmod(value, 10 ** 9)
        value = coarse * 10 ** 9 + (deep + nudge) % 10 ** 9
    return '0.%0*d' % (digits, value)


def _flag(value):
    return '1' if value else '0'


def _b64(value):
    """The base64 form the genuine Hidemium app writes.

    Measured against the 78 profiles the app wrote: the WebGL renderer, the
    MAC address of the registry and the labels of the media devices are base64
    in the config, not plain text. The core copies the string into the page
    as it is, so a plain renderer shows up as the base64 of the renderer.
    """
    return base64.b64encode(str(value).encode('utf8')).decode('ascii')


def _webgl_renderer(renderer, vendor=''):
    """The WebGL renderer in the form the genuine Hidemium app writes it.

    Measured against the 48 profiles the genuine app wrote: the renderer is
    the base64 of the ANGLE string, and inside that string the name of the
    GPU is base64 AGAIN. Measured against the core: the core decodes the
    stored value exactly once, so a value that is base64 only once reaches
    the page with the name of the GPU in clear text, and a value that is
    base64 twice reaches the page the way the genuine app writes it, with
    the name of the GPU still encoded.

    The second layer matters. A site that decodes the renderer of the page
    once sees `Adreno (TM) 618` in a NOVA profile and sees
    `QWRyZW5vIChUTSkgNjE4` in a genuine one, and that difference tells it
    in one request that the profile is not a phone.

    The ANGLE wrapper matters. Measured against the core: with the plain
    name of the GPU the page reads `Mali-G57 MC2`, and every Android phone
    reports the ANGLE string, because the driver of the GPU is what Chrome
    sees. A site that knows the shape of the string groups the profile
    with the desktops of the host, which report `ANGLE (Intel, Intel(R) HD
    Graphics ..., Direct3D11 ...)`. The version of OpenGL ES at the end is
    the one every mobile profile of the genuine app reports.
    """
    text = str(renderer)
    # The name of the GPU is the second part of the ANGLE string.
    if text.startswith('ANGLE'):
        gpu = text.split(', ')[1] if len(text.split(', ')) > 1 else text
    else:
        gpu = text
    angle = 'ANGLE (%s, %s, OpenGL ES 3.2)' % (vendor or 'Qualcomm', _b64(gpu))
    return _b64(angle)


def _uuid(seed):
    """The identifier of the profile, in the form the app writes it.

    Measured against the 78 profiles the genuine Hidemium app wrote: the
    key is `uuid` and the value is a UUID of the version four. The core
    reads it as the identity of the installation, so a fixed value lets a
    site group every profile of the installation with one request.
    """
    digest = hashlib.sha256((str(seed) + 'uuid').encode('utf8')).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    text = raw.hex()
    return '%s-%s-%s-%s-%s' % (text[0:8], text[8:12], text[12:16], text[16:20], text[20:32])


def _computer_name(seed):
    """The name the registry of the machine shows.

    Measured against the genuine Hidemium app: `DESKTOP-` followed by seven
    to ten letters and digits, different in every profile. A fixed name lets
    a site group every profile of the installation into one bucket.
    """
    # A phone has no DESKTOP- name: the page reads the name of the device
    # the way the user named it, so the name is a phone name of its own.
    alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    digest = hashlib.sha256((str(seed) + 'computer-name').encode('utf8')).digest()
    length = 7 + int.from_bytes(digest[:1], 'big') % 4
    return 'Galaxy-' + ''.join(alphabet[byte % 36] for byte in digest[1:1 + length])


def _device_memory(gb):
    """The RAM a Chrome reports for a phone with that much memory.

    The Network Information API only carries 0.25, 0.5, 1, 2, 4 and 8, so
    Chrome rounds the RAM of the machine down to the next power of two.
    Measured against the core: the core copies the value verbatim, so 12 GB
    would reach the page as `deviceMemory: 12`, a value no Chrome reports.
    """
    for allowed in (8, 4, 2, 1, 0.5, 0.25):
        if gb >= allowed:
            return allowed
    return 0.25


def _mac_address(seed):
    """A MAC address of the machine, in the form the app writes it.

    Measured against the genuine Hidemium app: the address is a plain
    `XX:XX:XX:XX:XX:XX` string, six uppercase hexadecimal octets, and it
    differs in every profile. The app does not set the locally administered
    bit, so the address keeps the shape of a manufacturer address.
    """
    octets = [seed_int(seed, 'mac-%d' % index, 0, 255) for index in range(6)]
    return ':'.join('%02X' % octet for octet in octets)


def _clean_list(value):
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [part.strip() for part in str(value or '').split(',')]
    return ','.join(item for item in items if item)


def webrtc_core_mode(settings, profile_mode=None):
    """The WebRTC mode of the core for the profile.

    The Hidemium v5 modes are `public`, `real`, `disabled` and
    `disable-stun`; the modes the NOVA interface used before
    (`proxy_only`, `none`, `default`) name the same thing, so they are
    translated instead of being sent to the core, which does not know them.
    """
    mode = (settings or {}).get('webrtc')
    if mode in WEBRTC_MODES:
        return mode
    return WEBRTC_BY_PROFILE.get(profile_mode or '', 'public')


def _chrome_version(fingerprint):
    """The Chrome build the profile announces.

    The UA of a Chrome on Android is frozen at `MAJOR.0.0.0`, so the build
    cannot be read from it: the profile keeps the build of its own in
    `navigator.chromeVersion`.

    Measured against the core: `hidemium.navigator.useragent.version_useragent`
    is the only key the core reads for the UA-CH brands and for the high
    entropy version, so it holds the full build, like the 48 genuine
    Hidemium profiles do (`149.0.7827.3`).
    """
    navigator = fingerprint.get('navigator', {}) or {}
    build = str(navigator.get('chromeVersion') or '').strip()
    if re.fullmatch(r'\d+\.\d+\.\d+\.\d+', build):
        return build
    ua = str(navigator.get('userAgent') or '')
    match = re.search(r'Chrome/(\d+\.\d+\.\d+\.\d+)', ua)
    return match.group(1) if match else CHROME_VERSION


def _ua_version(version):
    """The version a Chrome on Android writes in its user agent.

    Chrome on Android freezes the version of the UA at `MAJOR.0.0.0` and
    keeps the build in `navigator.userAgentData.fullVersionList`. The
    genuine Hidemium profiles do the same: every one of the 48 writes
    `Chrome/149.0.0.0` while the core of the app is a different build.

    Only the user agent string uses the frozen version. The
    `hidemium.navigator.useragent.version_useragent` key keeps the full
    build, like the genuine profiles do, and the core derives the greased
    brand of the UA-CH list from the major version only: measured against
    the core, 155 reports `Not(A:Brand` with the full build and with the
    frozen one alike.
    """
    match = re.match(r'(\d+)\.\d+\.\d+\.\d+', str(version or ''))
    return '%s.0.0.0' % match.group(1) if match else str(version or '')


def _language_list(value):
    """The language list of the profile, without the quality factors.

    The payload carries the value of the `Accept-Language` header, and
    that value holds the quality factors (`vi-VN,vi;q=0.9`). The core
    copies the string into `navigator.languages` as it is, so the page
    reads a language named `vi;q=0.9`, which no Chrome ever reports.

    Measured against the core: with the plain list the page reads
    `["vi-VN", "vi"]` and the server receives `vi-VN,vi;q=0.9`, which is
    what a real phone sends.
    """
    parts = []
    for part in str(value or '').split(','):
        name = part.split(';')[0].strip()
        if name and name not in parts:
            parts.append(name)
    return ','.join(parts)


def write_language_pref(directory, accept_language):
    """Make the header `Accept-Language` match the languages of the page.

    The header comes from the pref `intl.accept_languages` of Chromium and
    not from `hidemium.languages`, so without this the page reports
    `navigator.language` `vi-VN` while the server receives
    `Accept-Language: en-US,en;q=0.9`, the language of the machine of the
    host. A server that compares the two values sees a fake profile at
    once: no Chrome ever sends a header that disagrees with its own
    `navigator.languages`.

    Measured against the core: with the pref `vi-VN,vi` the header is
    `vi-VN,vi;q=0.9`, exactly what a phone with the Vietnamese language
    sends, and the page still reports `["vi-VN", "vi"]`.

    The pref file already holds the state of the profile, so the value is
    merged into it instead of replacing the file.
    """
    languages = _language_list(accept_language)
    if not languages:
        return
    folder = Path(directory) / 'Default'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'Preferences'
    prefs = {}
    if path.is_file():
        try:
            prefs = json.loads(path.read_text(encoding='utf8')) or {}
        except ValueError:
            prefs = {}
    if not isinstance(prefs, dict):
        prefs = {}
    intl = prefs.get('intl')
    prefs['intl'] = {**(intl if isinstance(intl, dict) else {}),
                     'accept_languages': languages}
    path.write_text(json.dumps(prefs), encoding='utf8')


def _device_identity(profile):
    """The model, the brand and the Android version of a profile.

    The device catalog writes the model of the phone as the manufacturer
    writes it ("SM-S921U", "Pixel 8"), so the name of the device is the
    value a site reads from `navigator.userAgentData.model`.
    """
    fingerprint = profile.get('config', {}).get('fingerprint', {}) or {}
    nav = fingerprint.get('navigator', {}) or {}
    screen = fingerprint.get('screen', {}) or {}
    # The catalog writes the brand of the preset; the payload only the name.
    brand = profile.get('device_preset_brand') or ''
    return {
        'model': str(nav.get('device') or profile.get('device_preset_name') or 'Android Device'),
        'brand': str(brand),
        'android': str(nav.get('osVersion') or '14.0.0'),
        # The hardware of the preset: the memory, the number of threads and
        # the number of touch points. The real Hidemium app writes them in
        # every profile, so a site reads the hardware of the phone.
        'memory': int(nav.get('deviceMemory') or 8),
        'threads': int(nav.get('hardwareConcurrency') or 8),
        'touch': int(nav.get('maxTouchPoints') or 5),
        'screen': screen,
        'webgl': fingerprint.get('webgl2', {}) or {},
        'languages': (fingerprint.get('languages', {}) or {}).get('acceptLanguage', ''),
        'timezone': (fingerprint.get('timezone', {}) or {}).get('nameTimezone', ''),
        # The screen of the phone and the density of its panel. Measured
        # against the 341 real Hidemium profiles: the app writes the width,
        # the height and the pixel ratio in every one of them.
        'screen_width': int(screen.get('width') or 393),
        'screen_height': int(screen.get('height') or 873),
        'pixel_ratio': str(screen.get('pixelRatio') or '2.75'),
        # The UA string of the profile. The real app writes it as a key of
        # its own, next to the parts of the UA it splits up.
        'useragent': str(nav.get('userAgent') or ''),
    }


def _serial_number(model, seed):
    """A serial number of an Android device.

    The format is the one the manufacturers use: a prefix of the brand, the
    model and a serial part.
    """
    prefix = ''.join(character for character in str(model).upper() if character.isalnum())[:6] or 'NOVA'
    return prefix + seed_hex(seed, 'serial', 10).upper()


def _media_devices(seed):
    """The camera and the microphone of the machine.

    Measured against the 78 profiles the genuine Hidemium app wrote: every
    profile carries its own identifiers, its own group and the three labels
    of the machine, and the identifiers are sixty four hexadecimal digits.
    The core copies these keys into `enumerateDevices()`, so a fixed
    identifier lets a site group every profile of the installation with one
    request.

    The labels are the names of the hardware of the profile, and the app
    writes them base64 encoded. NOVA names the hardware of a phone, because
    the name of an audio card of a desktop ("High Definition Audio Device",
    "Digital Audio (HDMI)") tells a site that the profile runs on a Windows
    machine, and one request groups the whole fleet.

    The labels still vary from profile to profile: one fixed label for every
    profile would make the whole installation show a single audio hardware,
    which a site can group with one request.
    """
    # The audio hardware of a phone: Chrome on Android names the input
    # `mic0` and the output `speaker`. A name of a desktop card ("High
    # Definition Audio Device", "Digital Audio (HDMI)") tells a site that
    # the profile runs on a Windows machine, which is the machine of the
    # worker, and one request groups the whole fleet.
    input_label = seed_pick(seed, 'media:audio-input-label',
                            ('mic0', 'mic1', 'default'))
    output_label = seed_pick(seed, 'media:audio-output-label',
                             ('speaker', 'default'))
    return {
        'hidemium.media_device.audio_input.id': seed_hex(seed, 'media:audio-input-id', 64),
        'hidemium.media_device.audio_input.group': seed_hex(seed, 'media:audio-input-group', 64),
        'hidemium.media_device.audio_input.label': _b64(input_label),
        'hidemium.media_device.audio_output.id': seed_hex(seed, 'media:audio-output-id', 64),
        'hidemium.media_device.audio_output.group': seed_hex(seed, 'media:audio-output-group', 64),
        'hidemium.media_device.audio_output.label': _b64(output_label),
        'hidemium.media_device.video_input.id': seed_hex(seed, 'media:video-input-id', 64),
        'hidemium.media_device.video_input.group': seed_hex(seed, 'media:video-input-group', 64),
        # The camera of a phone, never the audio card of the host.
        'hidemium.media_device.video_input.label': _b64(
            seed_pick(seed, 'media:video-input-label',
                      ('camera2 0, facing back', 'camera2 1, facing front'))),
        # The genuine app writes this key in all 48 profiles with the word
        # `default`; without it the core reads a different identity for the
        # devices.
        'hidemium.media_device.uid_media_device': 'default',
    }


def _font_list(seed):
    """The font list of the phone.

    Measured against the 78 profiles the genuine Hidemium app wrote: every
    profile carries a list of about 180 fonts, the list differs in every
    profile, and 44 of the fonts are the ones that differ. The core copies
    the list into the font table, and the page reads it: a font in the list
    has its own measured width and `document.fonts.check` reports it, a font
    that is not in the list is reported as missing.

    The list is therefore a website-observable axis, and a profile without a
    list reports the fonts of the Windows host, which no phone has and which
    every profile of the installation shares.
    """
    fonts = list(ANDROID_FONT_BASE)
    # The app draws a part of the varying fonts for every profile, 18 to 30
    # of the 44, so two profiles never carry the same list.
    count = seed_int(seed, 'fonts-count', 18, 30)
    pool = list(ANDROID_FONT_VARYING)
    # The order of the pool is shuffled by the seed, so the drawn fonts are
    # not always the first ones.
    digest = hashlib.sha256((str(seed) + 'fonts-shuffle').encode('utf8')).digest()
    pool.sort(key=lambda name: hashlib.sha256(digest + name.encode('utf8')).digest())
    fonts.extend(pool[:count])
    return fonts


def _battery(seed):
    """The battery of the phone.
    The core copies `level` into `navigator.getBattery().level` verbatim, and
    Chrome only ever reports a fraction between 0 and 1 there. A percentage
    like `51` would give the page a level of 51, which no phone has. The
    times are the seconds the Android framework reports.
    """
    charging = seed_int(seed, 'battery-charging', 0, 1)
    # The level with the decimals the Android framework reports. Chrome
    # copies the value into `BatteryManager.level` without rounding it.
    level = seed_int(seed, 'battery-level', 50000, 999999) / 1000000.0
    if charging:
        return {'charging': '1', 'level': '%.6f' % level,
                'charging_time': str(seed_int(seed, 'battery-charging-time', 300, 172800)),
                'discharging_time': '0'}
    # A phone that is not charging runs out in about the time the level says.
    capacity = seed_int(seed, 'battery-capacity', 2800, 5500)
    return {'charging': '0', 'level': '%.6f' % level, 'charging_time': '0',
            'discharging_time': str(int(capacity * level * 60))}


def apply_native(native, settings, seed, profile=None):
    """Write the Hidemium v5 settings into the native configuration.

    `native` is the dictionary that is serialised into config.hidemium.
    Only the keys the core knows are written; the profile payload stays
    the source of truth.

    Every value that can differ between two profiles is derived from the
    seed of the profile and from the preset of the profile. A value that
    would be a lie for the profile (a Windows registry key for an Android
    phone, a DNS mode this build cannot resolve) is not written.
    """
    profile = profile or {}
    fingerprint = profile.get('config', {}).get('fingerprint', {}) or {}
    identity = _device_identity(profile)
    renderer = identity['webgl'].get('unmaskedRenderer', '')
    # The identity of the browser, the keys the real Hidemium app writes in
    # every one of the 341 profiles it wrote. Measured there: they are
    # constants, so they are written as constants here. The core reads them
    # into `navigator.language`, `navigator.languages`, `Intl.DateTimeFormat`
    # and `navigator.userAgentData`, and a profile that leaves them out
    # reports the values of the machine instead.
    native['hidemium.languages'] = _language_list(identity['languages']) or 'vi-VN,vi,en-US,en'
    native['hidemium.timezone'] = identity['timezone'] or 'Asia/Ho_Chi_Minh'
    native['hidemium.navigator.pixel_ratio'] = identity['pixel_ratio']
    native['hidemium.navigator.screen.width'] = str(identity['screen_width'])
    native['hidemium.navigator.screen.height'] = str(identity['screen_height'])
    # The usable part of the screen. Measured against the core: without the
    # keys the page reads `800x600`, the screen of the Windows host, next
    # to the width and the height of the phone, and every profile of the
    # installation reports the same pair. The genuine app writes the width
    # and the height of the screen, and the height is twenty four pixels
    # smaller in two thirds of its profiles (the bar of the system).
    native['hidemium.navigator.screen.avail_width'] = str(identity['screen_width'])
    native['hidemium.navigator.screen.avail_height'] = str(
        identity['screen_height'] - (24 if seed_int(seed, 'screen-bar', 0, 2) else 0))
    if identity['useragent']:
        native['hidemium.navigator.useragent.useragent'] = identity['useragent']
    native['hidemium.chrome.version'] = _chrome_version(fingerprint)
    # The shape of the profile: which optional key groups it carries. The
    # real Hidemium app copies a whole template into a profile, so its
    # profiles fall into a handful of shapes. Writing every group in every
    # profile made every NOVA profile look like the rare full template of
    # the app, and a site could group the whole installation with one
    # request.
    groups = set(shape_groups(seed))
    # The canvas, the audio, the WebRTC and the connection stay in every
    # profile. Measured against the core: a profile without
    # `hidemium.canvas.value` paints with the seed of the host, so every
    # such profile reports the same canvas hash. Writing the seed is what
    # makes each profile a different device, which is the goal of NOVA, so
    # only the peripheral groups follow the shape.
    # The WebRTC mode decides whether the page reports the address of the
    # machine, and the connection keys stop the page from reading the
    # connection of the Windows host.
    # Canvas: the core only knows four modes. The seed is the hex form the
    # real Hidemium app writes, so two profiles never share a canvas.
    core_mode = CANVAS_CORE_MODE.get(settings.get('canvas', 'noise-brave'), 'noise-brave')
    canvas_seed = seed_hex_value(seed)
    if 'canvas' in groups:
        native['hidemium.canvas.mode'] = core_mode
        if core_mode != 'off':
            native['hidemium.canvas.value'] = canvas_seed
            native['hidemium.canvas.rgb'] = _flag(settings.get('canvas_rgb', True))
            # The size of the noise. The genuine app writes a width and a
            # height in every profile, and they differ in every profile
            # (41 and 38 distinct values). Measured against the core: the
            # keys change nothing the page reads on their own, but they are
            # part of the identity the app writes.
            native['hidemium.canvas.width'] = str(seed_int(seed, 'canvas-width', 5, 120))
            native['hidemium.canvas.height'] = str(seed_int(seed, 'canvas-height', 0, 117))
        # The image noise of the canvas.
        native['hidemium.canvas.image_noise'] = 'noise'
        native['hidemium.canvas.canvas_cpu'] = '0'
    else:
        for key in ('hidemium.canvas.mode', 'hidemium.canvas.value',
                    'hidemium.canvas.rgb', 'hidemium.canvas.width',
                    'hidemium.canvas.height', 'hidemium.canvas.image_noise',
                    'hidemium.canvas.canvas_cpu'):
            native.pop(key, None)
    # The audio uses the same seed as the canvas: the real Hidemium app
    # writes the same value to both keys.
    if 'audio' in groups:
        if settings.get('audio') == 'noise':
            native['hidemium.audio_context.mode'] = 'noise'
            native['hidemium.audio_context.value'] = canvas_seed
        else:
            native['hidemium.audio_context.mode'] = 'off'
    else:
        native.pop('hidemium.audio_context.mode', None)
        native.pop('hidemium.audio_context.value', None)
    # Client rects
    if 'rects' in groups:
        rects = settings.get('client_rects', 'off')
        native['hidemium.navigator.client_rects.mode'] = rects if rects in CLIENT_RECTS_MODES else 'off'
        if rects != 'off':
            native['hidemium.navigator.client_rects.value'] = str(seed_float(seed, 'rects'))
    else:
        native.pop('hidemium.navigator.client_rects.mode', None)
        native.pop('hidemium.navigator.client_rects.value', None)
    # WebRTC: the mode comes from the profile, the addresses from
    # network.hidemium.
    if 'webrtc' in groups:
        native['hidemium.webrtc.mode'] = webrtc_core_mode(settings, profile.get('webrtc_mode'))
    else:
        native.pop('hidemium.webrtc.mode', None)
    # WebGL metadata: when the metadata is off the real values of the
    # machine must show, so the vendor and the renderer are left out.
    if settings.get('webgl_metadata') == 'noise':
        webgl = identity['webgl']
        native['hidemium.webgl.mode'] = 'noise'
        if webgl.get('unmaskedVendor'):
            native['hidemium.webgl.vendor'] = webgl['unmaskedVendor']
        if webgl.get('unmaskedRenderer'):
            native['hidemium.webgl.renderer'] = _webgl_renderer(
                webgl['unmaskedRenderer'], webgl.get('unmaskedVendor', ''))
            # The limits of the GPU of the preset. Measured against the core:
            # without them the page reads the limits of the Windows machine
            # (16384) next to the name of a phone GPU that has 8192, and
            # that contradiction is visible in one request.
            #
            # The real Hidemium app writes `default` in every one of its 48
            # profiles, and `default` means "inherit the limits of the host",
            # so the genuine profiles leak the limits of the Intel machine of
            # the worker next to the name of a phone GPU. Writing the limits
            # of the family is what makes the profile self consistent.
            #
            # Only the 46 keys the core honours are written. The key
            # `webgl_extension` is NOT among them: it drops the list of the
            # extensions from 32 to 7.
            limits = gpu_limits(webgl['unmaskedRenderer'])
            for name in WEBGL_PARAM_KEYS:
                value = limits.get(name)
                if value is not None:
                    native['hidemium.webgl.webgl_param.' + name] = str(value)
    else:
        native.pop('hidemium.webgl.vendor', None)
        native.pop('hidemium.webgl.renderer', None)
        native.pop('hidemium.webgl.mode', None)
    # The native image noise of the WebGL. It is written in EVERY profile,
    # like the canvas and the audio, and not only in the shape that carries
    # the group: measured against the core, a profile without the pair paints
    # the pixels of the Windows host, so every such profile reports the same
    # draw hash and the site groups the whole fleet as one person.
    #
    # The value is a DECIMAL, not the hex seed of the canvas. Measured
    # against the core: `0.f84343fbaf` is read as zero and changes nothing,
    # while `0.11901111984325457` changes the pixels, is stable between two
    # runs and keeps every digit, so a million profiles stay apart.
    #
    # The renderer is part of the hash, so two profiles of the fleet on
    # different GPU rows never share a value. The nudge moves the deep
    # columns of the decimal; the guard against collisions uses it to
    # separate two profiles that landed on the same draw hash.
    if settings.get('webgl_image') == 'noise':
        native['hidemium.webgl.image_mode'] = 'noise'
        native['hidemium.webgl.image_value'] = webgl_image_value(
            seed, renderer, int(profile.get('webgl_image_nudge') or 0))
    else:
        native.pop('hidemium.webgl.image_mode', None)
        native.pop('hidemium.webgl.image_value', None)
    # The WebGL parameters are NOT written by default. Measured against a
    # real Hidemium profile: the real app writes no `webgl_param` key at all,
    # and a profile that writes them reports seven WebGL extensions where
    # the core reports thirty two. The limits of the GPU of the machine are
    # what the page must see next to the spoofed vendor, so the table of the
    # GPU families is only used when the user asks for it.
    if settings.get('webgl_parameter') == 'noise' and settings.get('webgl_params'):
        for name, value in (settings.get('webgl_params') or {}).items():
            if value in (None, ''):
                continue
            native['hidemium.webgl.webgl_param.' + str(name)] = value if isinstance(value, str) else str(value)
    # WebGPU: the vendor, the architecture, the features and the limits of
    # the GPU of the preset.
    #
    # Measured against the core: the core honours every one of the 36
    # `webgpu.param.*` limits (the buffer size of the host, 1 GB, becomes
    # 256 MB), the vendor and the architecture (swiftshader becomes
    # qualcomm/Adreno) and the list of the features.
    #
    # The real Hidemium app writes `default` in every one of its 48
    # profiles, and `default` means "inherit the host", so the genuine
    # profiles report the limits of the Windows machine next to the name of
    # a phone GPU. Writing the limits of the family is what makes the
    # profile self consistent.
    if settings.get('webgpu') == 'noise':
        family = WEBGPU_FAMILIES[webgpu_family(identity['webgl'].get('unmaskedRenderer'))]
        native['hidemium.webgpu.vendor'] = family['vendor']
        native['hidemium.webgpu.architecture'] = family['architecture']
        native['hidemium.webgpu.features'] = family['features']
        for name, value in family['limits'].items():
            native['hidemium.webgpu.param.' + name] = str(value)
        for name, value in (settings.get('webgpu_params') or {}).items():
            if value in (None, ''):
                continue
            native['hidemium.webgpu.param.' + str(name)] = value if isinstance(value, str) else str(value)
    # Codecs
    if 'privacy' in groups:
        native['hidemium.codecs.audio'] = 'noise' if settings.get('codecs') == 'noise' else 'off'
        native['hidemium.codecs.video'] = 'noise' if settings.get('codecs') == 'noise' else 'off'
        # SSL: the core reads the number, but the TLS signature stays the same.
        if settings.get('ssl') == 'noise':
            native['hidemium.ssl.random_number'] = str(seed_float(seed, 'ssl'))
        else:
            native.pop('hidemium.ssl.random_number', None)
        # Plugins: the values are the ones the Hidemium app writes for the PDF
        # viewer of Chrome.
        if settings.get('plugins') == 'noise':
            native['hidemium.plugin.name'] = 'PDF Viewer'
            native['hidemium.plugin.description'] = 'Portable Document Format'
            native['hidemium.plugin.file'] = 'internal-pdf-viewer'
    else:
        for key in ('hidemium.codecs.audio', 'hidemium.codecs.video',
                    'hidemium.ssl.random_number', 'hidemium.plugin.name',
                    'hidemium.plugin.description', 'hidemium.plugin.file'):
            native.pop(key, None)
    # The registry of the machine. Measured against the 78 profiles the
    # genuine Hidemium app wrote: the name of the machine and its MAC
    # address differ in every profile, so a fixed pair would let a site
    # group every profile of the installation into one bucket.
    native['hidemium.registry.computer_name'] = _computer_name(seed)
    native['hidemium.registry.mac_address'] = _b64(_mac_address(seed))
    # Media devices: the core reads exactly twelve `hidemium.media_device.*`
    # keys and copies them into `enumerateDevices()`. The identifiers and
    # the groups differ in every profile of the genuine app.
    #
    # The masking is NOT a shape. Measured against the 48 genuine profiles:
    # every one of them writes `hidemium.media_device.enabled_masking` with
    # the word `true` and the three devices with their own identifiers. A
    # profile without the keys does not hide the hardware, it publishes it:
    # the page reads the card of the machine of the host, with the label
    # "Generic High Definition Audio Device" and the identifier "default",
    # next to a user agent that announces an Android phone. That is the
    # strongest signal of the whole fleet, and it is one request.
    masking = settings.get('media_masking', 'noise')
    # Measured against the core: the genuine Hidemium app writes the word
    # `true` in every one of its profiles. The core reads the word, not the
    # digit, so with the digit `1` the core keeps the devices of the Windows
    # host and the page sees the real machine.
    if masking != 'disable':
        native['hidemium.media_device.enabled_masking'] = 'true'
        native['hidemium.media_device.mic.status'] = settings.get('mic_permission', 'default')
        native['hidemium.media_device.camera.status'] = settings.get('camera_permission', 'default')
        native.update(_media_devices(seed))
    else:
        native.pop('hidemium.media_device.enabled_masking', None)
        native.pop('hidemium.media_device.mic.status', None)
        native.pop('hidemium.media_device.camera.status', None)
        for name in ('audio_input', 'audio_output', 'video_input'):
            for part in ('id', 'label', 'group'):
                native.pop('hidemium.media_device.%s.%s' % (name, part), None)
    # The real Hidemium app writes the battery only when it spoofs it.
    if 'battery' in groups and settings.get('battery') == 'noise':
        for name, value in _battery(seed).items():
            native['hidemium.value.battery.' + name] = value
    else:
        for name in ('charging', 'charging_time', 'discharging_time', 'level'):
            native.pop('hidemium.value.battery.' + name, None)
    # Network information: the core copies these into `navigator.connection`.
    # Measured against the core: the keys are applied verbatim, so the
    # numbers a site reads are the numbers of the profile. They follow the
    # effective type of the connection, so a 4G phone reports a fast link
    # and a 2G phone a slow one.
    #
    # The connection of the phone. Measured against the 78 profiles the
    # genuine Hidemium app wrote: it writes four keys, and the kind of the
    # link and the round trip time differ in every profile, so a site can
    # read them to group the profiles. The core copies the values into
    # `navigator.connection` as they are, measured against the core.
    effective_type = settings.get('effective_type') or ''
    if 'net' in groups:
        # The five values of the connection have to agree with each other.
        # A random type next to a random round trip time gives pairs that
        # do not exist (`type: bluetooth` on a phone, `slow-2g` next to
        # 87 Mbps), and a site reads that contradiction in one request.
        # The speed is drawn first and the label follows from it, the way
        # Chrome derives it from the speed it measured.
        native['hidemium.network.type'] = (
            'cellular' if seed_int(seed, 'network-kind', 0, 1) == 0 else 'wifi')
        if effective_type in ('4g', '3g', '2g', 'slow-2g'):
            # The owner pinned a kind of link, so the numbers of the link
            # are drawn from the range that belongs to that label.
            low, high, floor = NETWORK_LIMITS[effective_type]
            rtt = low + seed_int(seed, 'network-rtt', 0, high - low)
            downlink = floor + seed_int(
                seed, 'network-downlink', 0, int((99.9999 - floor) * 10000)) / 10000.0
            native['hidemium.network.effective_type'] = effective_type
        else:
            rtt = seed_int(seed, 'network-rtt', 0, 999)
            downlink = seed_int(seed, 'network-downlink', 1, 999999) / 10000.0
            if downlink >= 10.0 and rtt < 70:
                native['hidemium.network.effective_type'] = '4g'
            elif downlink >= 0.7 and rtt < 700:
                native['hidemium.network.effective_type'] = '3g'
            elif downlink >= 0.075:
                native['hidemium.network.effective_type'] = '2g'
            else:
                native['hidemium.network.effective_type'] = 'slow-2g'
        native['hidemium.network.rtt'] = str(rtt)
        native['hidemium.network.downlink'] = str(round(downlink, 4))
        # The data saver. The genuine app writes it in half of its profiles
        # (`on`) and leaves it at the default in the other half. The core
        # copies the value into `navigator.connection.saveData`.
        if seed_int(seed, 'save-data', 0, 1):
            native['hidemium.network.save_data'] = 'on'
        else:
            native.pop('hidemium.network.save_data', None)
    else:
        # The genuine app writes the connection in 6.5% of its profiles only.
        # Without the keys the page reads the connection of the Windows
        # host, which is what the app's own plain profiles do too.
        for key in ('hidemium.network.effective_type', 'hidemium.network.downlink',
                    'hidemium.network.type', 'hidemium.network.rtt',
                    'hidemium.network.save_data'):
            native.pop(key, None)
    # `downlinkmax` is never written: measured against the core, the value
    # `undefined` makes the core hang and the WebSocket of the runtime
    # probe times out.
    native.pop('hidemium.network.downlinkmax', None)
    # DNS and the anti-bot bypass
    # The core knows hidemium.dns.mode and hidemium.dns.domain, but the
    # captured profiles of the Hidemium app never write them: the core then
    # resolves with the resolver of the machine, which is what a profile on a
    # proxy needs. The option stays in the tab, but the core keeps its own
    # behaviour instead of a mode this build cannot resolve.
    native.pop('hidemium.dns.mode', None)
    native.pop('hidemium.dns.domain', None)
    # Port scanning protection
    ports = _clean_list(settings.get('ports_protect', ''))
    if 'ports' in groups and ports:
        native['hidemium.port_protect'] = ports
    else:
        native.pop('hidemium.port_protect', None)
    # The font list of the phone. Measured against the 78 profiles the
    # genuine app wrote: the list is what the page reads (a font in the
    # list has its own measured width, a font that is not in it is
    # reported as missing), and the list differs in every profile. The
    # earlier note that the key must not be written was wrong: it was
    # based on a font list of the NOVA interface that did not hold Arial
    # and Times New Roman, while the list of the genuine app holds both.
    fonts = _font_list(seed)
    native['hidemium.fonts'] = ','.join(fonts)
    native['hidemium.fontface'] = ANDROID_FONTFACE
    # `hidemium.fonts_value` changes nothing on its own: measured against
    # the core, the same list with two different values gives the same
    # widths, so the key is not written.
    # `navigator.appName`, `navigator.appCodeName` and `navigator.vendor`
    # are NOT written. Measured: the core already reports the real Chrome
    # values (`Netscape`, `Mozilla`, `Google Inc.`), and writing
    # `hidemium.app.app_name` replaces them with the given value, so a
    # profile would announce a phone as its application name. The same
    # applies to `navigator.product`: writing `Gecko` adds a `Gecko` entry
    # to the UA-CH brand list, which no real Chrome reports.
    # The UA metadata of the phone. `bitness` is what
    # `navigator.userAgentData` reports, so it follows the preset: a 64 bit
    # phone reports `64`.
    native['hidemium.navigator.useragent.bitness'] = '64'
    # `architecture` is kept on the value of the preset. Measured against the
    # core: any value in this key makes the page report an empty
    # `architecture`, and only the absence of the key lets the core report the
    # `x86` of the Windows host, which is a leak of the machine. The DevTools
    # override of the app sends the real Android value to every target, page
    # and worker, so the page reads `arm` there.
    native['hidemium.navigator.useragent.architecture'] = 'arm'
    # `wow64` is the word `false` in every profile of the genuine app.
    native['hidemium.navigator.useragent.wow64'] = 'false'
    native['hidemium.navigator.useragent.version_useragent'] = _chrome_version(fingerprint)
    # The model of the phone. Measured: the core copies this into
    # `navigator.userAgentData.model`, and without the key the field is
    # empty while the page still announces an Android phone.
    native['hidemium.navigator.useragent.model'] = identity['model']
    # The full version of the browser: the core reports it in the UA-CH
    # high entropy values.
    native['hidemium.navigator.useragent.fullversion'] = _chrome_version(fingerprint)
    # `manufacturer` is NOT written. Measured: writing it makes the core
    # report an empty `architecture` in the UA-CH values, and the real
    # Hidemium app keeps the value of the machine (`Google Inc.`).
    # The client rects of the phone: the noise needs a seed of its own.
    if 'rects' in groups:
        native['hidemium.navigator.client_rects.value'] = str(seed_float(seed, 'rects', 12))
    else:
        native.pop('hidemium.navigator.client_rects.value', None)
    # The memory of the phone in GB, as the Android framework reports it.
    # Measured against the 341 real Hidemium profiles: the app writes
    # `hidemium.navigator.physical_memory` in every one of them, with the
    # same value `8`.
    native['hidemium.navigator.physical_memory'] = '8'
    if settings.get('physical_memory'):
        native['hidemium.navigator.physical_memory'] = str(int(settings['physical_memory']))
    # The keyboard layout of the phone follows the language of the profile.
    layout = (identity['languages'].split(',')[0] if identity['languages'] else '') or 'en-US'
    native['hidemium.navigator.keyboard_layout'] = layout
    # The hardware of the phone. Measured against the core: these five keys
    # are read by the page, `navigator.deviceMemory`,
    # `navigator.hardwareConcurrency`, `navigator.maxTouchPoints` and the
    # UA-CH `mobile` flag. The real Hidemium app writes them in every
    # profile, so without them every profile of the machine reported the
    # same 8 GB, the same 8 threads and the same 5 touch points.
    # The Network Information API only ever reports 0.25, 0.5, 1, 2, 4 or
    # 8 GB, and Chrome rounds the RAM of the machine down to the next power
    # of two. Measured against the core: the core copies the value verbatim,
    # so a preset with 12 GB would show `deviceMemory: 12`, a value no
    # Chrome ever reports.
    native['hidemium.navigator.device_memory'] = str(_device_memory(identity['memory']))
    native['hidemium.navigator.hardware_concurrency'] = str(identity['threads'])
    native['hidemium.navigator.max_touch_point'] = str(identity['touch'])
    # The mobile flag follows the user agent, not the kind of the device.
    # The core copies the flag into `navigator.userAgentData.mobile`, so a
    # flag of `1` next to a user agent without the word "Mobile" is a
    # contradiction a site can read. The catalog writes the flag into the
    # user agent of the preset, so the payload carries it.
    native['hidemium.navigator.is_mobile'] = '1' if 'Mobile' in identity['useragent'] else '0'
    # The pointer of the phone. Measured against the core: `is_tablet` is
    # the key that gives the page a coarse pointer and no hover, and the
    # value has to be the word `true`. Without it the page reads
    # `pointer: fine` and `hover: hover` on a phone, which no touch screen
    # ever reports. The genuine app writes the key in every profile.
    native['hidemium.navigator.is_tablet'] = 'true'
    # The system of the phone: the core copies these into the UA-CH
    # `platform` and `platformVersion` values.
    native['hidemium.navigator.os.platform_os'] = 'Android'
    native['hidemium.navigator.os.platforms_version'] = identity['android']
    # The simple switches
    if 'bluetooth' in groups:
        native['hidemium.bluetooth'] = _flag(settings.get('bluetooth') == 'noise')
    else:
        native.pop('hidemium.bluetooth', None)
    if 'privacy' in groups:
        native['hidemium.disable_image'] = _flag(settings.get('disable_image'))
    else:
        native.pop('hidemium.disable_image', None)
    # Measured: the genuine Hidemium app writes `hidemium.navigator.donottrack`
    # with the value `1` in 6.7% of its profiles, and the page reads
    # `navigator.doNotTrack` as "1". Writing the key is what makes the page
    # report it; leaving it out leaves the value null.
    if 'privacy' in groups and settings.get('do_not_track') == 'on':
        native['hidemium.navigator.donottrack'] = '1'
    else:
        native.pop('hidemium.navigator.donottrack', None)
    # Measured: the genuine app writes the word `default` for this key in
    # every one of its profiles, and the page reads `cookieEnabled: true`
    # either way, so the value itself is not visible in the page.
    if 'privacy' in groups:
        native['hidemium.app.cookie_enable'] = 'default'
        native['hidemium.popup.mode'] = settings.get('popup_mode', 'block')
    else:
        native.pop('hidemium.app.cookie_enable', None)
        native.pop('hidemium.popup.mode', None)
    # The storage of the phone: a phone with a storage of 64 GB reports
    # about 40 GB free, a phone of 256 GB about 180 GB.
    if settings.get('storage_mode') == 'noise':
        # Measured against the core: the mode is the word `on`, as the
        # genuine Hidemium app writes it. With the mode `1` the core reports
        # the quota of the Windows host (10737418240) for every profile, so
        # one request groups every profile of the installation. With `on` the
        # quota follows the profile and differs in every one of them.
        native['hidemium.storage.mode'] = 'on'
        # The size of the storage of the phone, in bytes. The range is the one
        # the genuine app uses, see STORAGE_SIZE_MIN and STORAGE_SIZE_MAX.
        size = settings.get('storage_size') or seed_int(
            seed, 'storage', STORAGE_SIZE_MIN, STORAGE_SIZE_MAX)
        native['hidemium.storage.size'] = str(int(size))
    if 'geo' in groups:
        native['hidemium.geolocation.status'] = settings.get('geolocation_behaviour', 'prompt')
        # The accuracy of the position, in metres. The real Hidemium app
        # writes `100`, the accuracy of a city level position of a phone.
        native['hidemium.geolocation.accuracy'] = str(settings.get('geolocation_accuracy') or 100)
    else:
        native.pop('hidemium.geolocation.status', None)
        native.pop('hidemium.geolocation.accuracy', None)
    if settings.get('hide_profile_name'):
        native['hidemium.name_profile'] = str(profile.get('name', 'NOVA'))
    # The signature of the profile. The genuine app writes it in every
    # profile and it differs in every profile (48 distinct values): it is
    # the hex of the identifier of the profile. The core reads it as the
    # identity of the machine, so a fixed value lets a site group every
    # profile of the installation with one request.
    native['hidemium.signature'] = seed_hex(seed, 'signature', 64)
    # The identifier of the profile. The genuine app writes it in every
    # profile and it differs in every profile.
    native['uuid'] = _uuid(seed)
    # The platform string has to be the one the core reads verbatim.
    native['hidemium.navigator.useragent.platforms'] = ANDROID_PLATFORM
    return native


def command_flags(settings):
    """The command line switches of the Hidemium v5 launcher.

    The switches come from app.asar; they are the part of the settings the
    core reads from the process, not from config.hidemium.
    """
    flags = []
    if settings.get('disable_window_open'):
        flags.append('--hidemium-disable-window-open')
    if settings.get('enable_memory_saving_mode'):
        flags.append('--hidemium-enable-memory-saving')
    if settings.get('disable_ai_mode'):
        flags.append('--hidemium-disable-ai-mode')
    if settings.get('disable_avatar', True):
        flags.append('--hidemium-disable-avatar')
    if settings.get('incognito'):
        flags.append('--incognito')
    if settings.get('disable_paskey'):
        flags.append('--hidemium-disable-popup-passkey')
    if settings.get('enable_translation'):
        flags.append('--hidemium-translate')
    if settings.get('enable_accept_extension'):
        flags.append('--hidemium-auto-accept-ext')
    if settings.get('disable_window_hello'):
        flags.append('--hidemium-disable-window-hello')
    if settings.get('always_allow_popups'):
        flags.append('--hidemium-popup-mode')
    if settings.get('disable_app_launch_from_websites'):
        flags.append('--hidemium-disable-popup')
    if settings.get('disable_machine_id'):
        flags.append('--hidemium-disable-machine-id')
    if settings.get('enable_video_stream_spoofing'):
        flags.append('--hidemium-enable-video-stream-spoofing')
    if not settings.get('enable_webgl2', True):
        flags.append('--disable-webgl2')
    if settings.get('enable_swiftshader'):
        flags.append('--disable-gpu')
    if settings.get('disable_css_animation'):
        flags.append('--disable-css-animation')
    if settings.get('disable_autofill_popup'):
        flags.append('--hidemium-disable-autofill')
    if settings.get('restore_session', True):
        flags.append('--restore-last-session')
    if settings.get('akamai_bypass_dns'):
        flags.append('--akamai-bypass-dns')
    if settings.get('hide_profile_name', True):
        flags.append('--hidemium-hide-profilename')
    if settings.get('clipboard_access') in CLIPBOARD_ACCESS:
        flags.append('--hidemium-clipboard-mode=' + settings['clipboard_access'])
    if settings.get('website_notification') in NOTIFICATION_PERMISSION:
        flags.append('--hidemium-notification=' + settings['website_notification'])
    if settings.get('password_autosave') in PASSWORD_AUTOSAVE:
        flags.append('--hidemium-autosavepassword=' + settings['password_autosave'])
    custom = str(settings.get('custom_launch_parameters') or '').strip()
    if custom:
        flags.extend(part for part in custom.split() if part.startswith('--'))
    return flags


def network_hidemium(settings, geo=None, public_ip='', local_ip=''):
    """Build network.hidemium: the core reads the IP values as plain literals.

    The core parses `webrtc.public_ip` and `machine.ip_public` as IP literals
    (`P2PSocketUdp: webrtc.public_ip is not an IP literal`); base64 is read as
    a number and comes out as a wrong address.
    """
    geo = geo or {}
    lines = []
    if public_ip:
        lines.append('webrtc.public_ip:' + public_ip)
        lines.append('machine.ip_public:' + public_ip)
    if local_ip:
        lines.append('webrtc.local_ip:' + local_ip)
    if geo.get('timezone'):
        lines.append('hidemium.timezone:' + geo['timezone'])
    if geo.get('latitude') is not None:
        lines.append('hidemium.geolocation.latitude:' + str(geo['latitude']))
    if geo.get('longitude') is not None:
        lines.append('hidemium.geolocation.longitude:' + str(geo['longitude']))
    if geo.get('accuracy') is not None:
        lines.append('hidemium.geolocation.accuracy:' + str(geo['accuracy']))
    if geo.get('languages'):
        lines.append('hidemium.languages:' + geo['languages'])
    return '\n'.join(lines) + ('\n' if lines else '')
