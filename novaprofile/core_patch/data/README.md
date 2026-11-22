# The device datasets

The fleet catalogue is built from *real* devices.  The specifications
come from three public dumps of the GSMArena device pages:

| File | Source | Records | Character |
|---|---|---|---|
| gsm.csv | cigarplug/scrape-gsma | 10.679 | the oldest devices, densest chipset coverage |
| khal_data.csv | KhalSCI/GSMARENA_Scraper | 8.792 | the widest brand coverage, nested spec dictionaries |
| ishumann.csv | ishumann/gsmarena-webscrapping | 4.000+ | the newest devices, flat columns |

devices.jsonl is the **merged, deduplicated** result: 10.923 real
devices, each with a real SoC, a real GPU, a real panel and a real
memory configuration.  It is produced by:

`
python novaprofile/core_patch/normalize_device_data.py \
    --gsm gsm.csv --khal khal_data.csv --ishumann ishumann.csv \
    --output novaprofile/core_patch/data/devices.jsonl
`

The three raw dumps are **not** committed -- they are 33 MB of scraped
HTML rendered as CSV, and the merged file is what the product needs.
The scripts that fetch them are etch_gsmarena.py (the live reader)
and 
ormalize_device_data.py (the merger).

## Why three sources

No single public dataset carries ten thousand devices with a chipset, a
GPU and a panel.  The sources overlap heavily and each one misses
devices the others carry, so their union is larger than any of them
alone.

## The record shape

`json
{"brand": "Samsung", "model": "Galaxy S24", "chipset": "Exynos 2400",
 "gpu_renderer": "Xclipse 940", "resolution": "1080 x 2340 pixels, 19.5:9 ratio (~416 ppi density)",
 "display_size": "6.2 inches", "memory_internal": "256GB 12GB RAM",
 "os_text": "Android 14", "announced": "2024, January", "source_id": "khal:1234"}
`

Every field is what the manufacturer published.  A device that lacks a
chipset, a GPU or a resolution is **dropped**, not guessed: a profile
whose GPU contradicts its SoC is worse than no profile at all.
