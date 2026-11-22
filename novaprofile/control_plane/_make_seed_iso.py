"""Create the cloud-init seed ISO for the build VM.

The Ubuntu cloud image boots with no password and no way in: it
expects a seed disk that carries the user, the SSH key and the
instructions to grow the partition.  This script writes that disk.

The image itself is not touched here -- it is prepared by the shell
steps around this script -- because a cloud image is a qcow2 with a
partition table inside it, and the tools that grow it live in the
guest.
"""

from __future__ import annotations

import io
import pathlib

import pycdlib

USER_DATA = """#cloud-config
hostname: nova-build
manage_etc_hosts: true

users:
  - name: nova
    groups: sudo, adm
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - {key}

growpart:
  mode: auto
  devices: ['/']
  ignore_growroot_disabled: false

resize_rootfs: true

package_update: false
packages: []

runcmd:
  - [ sh, -c, "echo READY > /var/tmp/nova-ready" ]
"""

META_DATA = """instance-id: nova-build-001
local-hostname: nova-build
"""


def build_iso(path: pathlib.Path, public_key: str) -> None:
    """Write the seed ISO to ``path``."""

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, vol_ident="CIDATA")

    user_data = USER_DATA.format(key=public_key.strip()).encode("utf-8")
    meta_data = META_DATA.encode("utf-8")

    for name, payload in (
        ("user-data", user_data),
        ("meta-data", meta_data),
    ):
        # The ISO 9660 level 1 name has to be upper case and made of
        # the characters the standard allows; the Joliet name is the
        # one cloud-init reads, and it is free to spell it properly.
        iso.add_fp(
            io.BytesIO(payload),
            len(payload),
            f"/{name.upper().replace('-', '_')};1",
            joliet_path=f"/{name}",
        )

    iso.write(str(path))
    iso.close()


if __name__ == "__main__":
    import sys

    target = pathlib.Path(sys.argv[1])
    key = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
    build_iso(target, key)
    print(f"wrote {target} ({target.stat().st_size} bytes)")
