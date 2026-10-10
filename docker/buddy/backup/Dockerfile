# Backup service of Buddy's release stack (see buddy_backup.py and AGENTS.md "Backups (release only)").
# Built FROM the same Python base image as Buddy (the `base` stage of the repository Dockerfile), so the SQLite
# library that copies webui.db is the one the release instance writes it with. Keep the two tags in sync.
FROM python:3.11-slim-bookworm

# Perforce command-line client (official static build for linux x86_64), pinned to P4/LINUX26X86_64/2025.2/3086021.
# Perforce republishes patch builds at the same URL: when the checksum stops matching, compare it with the `p4` line
# of SHA256SUMS in the same directory, check `p4 -V`, and update the checksum deliberately.
ADD --checksum=sha256:8f3c2688c537f614fd5ae94a1edb0110c35a08bca4bc856c4cdc5e29b68e457e --chmod=0755 \
    https://cdist2.perforce.com/perforce/r25.2/bin.linux26x86_64/p4 /usr/local/bin/p4
RUN p4 -V | tail -1 && mkdir -p /work

COPY buddy_backup.py /usr/local/bin/buddy_backup.py
RUN python -m py_compile /usr/local/bin/buddy_backup.py

WORKDIR /work
ENTRYPOINT ["python", "/usr/local/bin/buddy_backup.py"]
CMD ["serve"]
