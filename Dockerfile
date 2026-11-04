# check=skip=FromPlatformFlagConstDisallowed
# pysrim2 + SRIM-2013 under wine, for linux/amd64.
#   podman run --rm -v "$PWD:/data:Z" ghcr.io/fsibt/pysrim2:main python /data/run.py
# (Docker 29 also needs --security-opt seccomp=srim/data/seccomp-docker.json; see docs/container.md)
FROM --platform=linux/amd64 debian:trixie-slim

LABEL org.opencontainers.image.source="https://github.com/FSIBT/pysrim2" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="pysrim2 + SRIM-2013 (SRIM © SRIM.com (James F. Ziegler) — free for non-commercial use only; see /opt/srim/Data/LEGAL.rtf)"

ARG DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      wine wine32:i386 xvfb xauth 7zip cabextract unzip curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

# uv, pinned by version and digest.
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /uvx /usr/local/bin/

# /opt itself belongs to srim: the installer stages SRIM in a sibling of
# /opt/srim and renames it into place; /opt/srim, /opt/wine and /opt/pysrim2
# are then created (and owned) by srim.
RUN useradd --create-home --uid 1000 --shell /bin/bash srim \
 && mkdir -p /data \
 && chown srim:srim /opt /data

ENV PYSRIM2_SRIM_DIR=/opt/srim \
    PYSRIM2_WINEPREFIX=/opt/wine \
    PYSRIM2_HOME=/opt/pysrim2 \
    WINEDEBUG=-all \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

USER srim

# Python 3.15 and pysrim2 from the build context (plus pytest, so that
# `docker run IMAGE pytest -m srim` works with the tests mounted).
RUN uv venv /opt/venv --python 3.15
RUN --mount=type=bind,source=.,target=/tmp/src,rw \
    uv pip install /tmp/src pytest pytest-mock

# The same code path users run: wine prefix (OCX + VB5 runtimes) and SRIM-2013.
RUN xvfb-run -a -s "-screen 0 1024x768x24" pysrim2 install wine srim --yes \
 && test -f /opt/srim/Data/LEGAL.rtf \
 && xvfb-run -a -s "-screen 0 1024x768x24" pysrim2 doctor \
 && (wineserver -k || true) \
 && rm -rf /tmp/.X*-lock /tmp/.X11-unix /tmp/wine-* /tmp/.wine-*

WORKDIR /data
# tini as PID 1: xvfb-run hangs waiting for Xvfb's SIGUSR1 when it is PID 1.
ENTRYPOINT ["tini", "--", "xvfb-run", "-a", "-s", "-screen 0 1024x768x24"]
CMD ["python"]
