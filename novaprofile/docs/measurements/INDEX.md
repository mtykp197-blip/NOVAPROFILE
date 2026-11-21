# Chỉ mục phép đo

_Sinh tự động từ `data/`. Đừng sửa tay — chạy `python -m novaprofile.scripts.measurements` để cập nhật._

| Slug | Thời điểm | Dùng lại | Số liệu chính | Kết luận |
| --- | --- | --- | --- | --- |
| `entropy-budget` | 2026-11-22T00:38 | ✅ | `all_axes_clear`=True, `axes`={'sig_audio': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_canvas': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_device': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_devices': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_fonts': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_full': {'birthday_p_at_target': 0.0, 'bits': 1280, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_rects': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_sensors': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_storage': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_webgl_draw': {'birthday_p_at_target': 0.0, 'bits': 184, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_webgl_params': {'birthday_p_at_target': 0.0, 'bits': 184, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}, 'sig_webgpu': {'birthday_p_at_target': 0.0, 'bits': 128, 'clears_threshold': True, 'distinct_in_sample': 200, 'sample': 200}}, `required_bits`=26.58, `target_fleet`=100000000, +2 more | Every identifying axis clears the 26.6-bit fleet threshold; the weakest is 'sig_device' at 128.0 bits. |

<details><summary>Lịch sử entropy-budget (3 lần trước)</summary>

- `2026-11-21T23:36` — Every identifying axis clears the 26.6-bit fleet threshold; the weakest is 'sig_device' at 128.0 bits.
- `2026-11-22T00:15` — Every identifying axis clears the 26.6-bit fleet threshold; the weakest is 'sig_device' at 128.0 bits.
- `2026-11-22T00:24` — Every identifying axis clears the 26.6-bit fleet threshold; the weakest is 'sig_device' at 128.0 bits.

</details>
| `fleet-audit` | 2026-11-22T00:44 | ✅ | `coherent_passed`=True, `dimensions_passed`=4, `dimensions_total`=4, `errors`=0, +5 more | All 4 audit dimensions pass across 2000 profiles with 25 warning(s). |
| `scale-collision` | 2026-11-22T00:43 | ✅ | `axes_checked`=12, `collisions_sig_audio`=0, `collisions_sig_canvas`=0, `collisions_sig_device`=0, +27 more | Zero collisions across all 12 guarded axes at 2000000 profiles. |

<details><summary>Lịch sử scale-collision (2 lần trước)</summary>

- `2026-11-21T23:42` — Zero collisions across all 12 guarded axes at 2000000 profiles.
- `2026-11-22T00:29` — Zero collisions across all 12 guarded axes at 2000000 profiles.

</details>
| `test-suite` | 2026-11-22T00:39 | ✅ | `pytest_failed`=0, `pytest_passed`=559, `ruff_clean`=True, `ruff_violations`=0 | 559 tests pass and the linter is clean. |

<details><summary>Lịch sử test-suite (5 lần trước)</summary>

- `2026-11-21T23:36` — 0 tests pass and the linter is clean.
- `2026-11-21T23:36` — 0 tests pass and the linter is clean.
- `2026-11-21T23:37` — 545 tests pass and the linter is clean.
- `2026-11-22T00:15` — 545 tests pass and the linter is clean.
- `2026-11-22T00:25` — 546 tests pass and the linter is clean.

</details>
