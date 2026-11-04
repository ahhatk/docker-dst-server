ARG BASE_IMAGE=debian:bookworm-slim
FROM $BASE_IMAGE
LABEL maintainer="James Swineson <docker@public.swineson.me>"

ARG DEBIAN_FRONTEND=noninteractive
ARG STEAMCMD_URL=https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz
ARG STEAMCMD_PATH=/opt/steamcmd/steamcmd.sh
ARG DST_DOWNLOAD=1
ARG LANG=C.UTF-8
ARG LC_ALL=C.UTF-8
ARG DST_USER=dst
ARG DST_GROUP=dst
ARG DST_USER_DATA_PATH=/data

# install packages
RUN dpkg --add-architecture i386 \
    && apt-get update -y \
    && apt-get install -y --no-install-recommends ca-certificates lib32stdc++6 wget tar supervisor \
    && (apt-get install -y --no-install-recommends libcurl3-gnutls libcurl3-gnutls:i386 || apt-get install -y --no-install-recommends libcurl3t64-gnutls libcurl3t64-gnutls:i386) \
    && (test -e /usr/lib/x86_64-linux-gnu/libcurl-gnutls.so.4 || test -e /lib/x86_64-linux-gnu/libcurl-gnutls.so.4) \
    && (test -e /usr/lib/i386-linux-gnu/libcurl-gnutls.so.4 || test -e /lib/i386-linux-gnu/libcurl-gnutls.so.4) \
    && (apt-get install -y --no-install-recommends lib32gcc-s1 || apt-get install -y --no-install-recommends lib32gcc1) \
    && apt-get autoremove -y \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

# create data directory
RUN mkdir -p "${DST_USER_DATA_PATH}" \
    # Add unprivileged user
    && ( groupadd "${DST_GROUP}" || true ) \
    && ( useradd -g "${DST_GROUP}" -d "${DST_USER_DATA_PATH}" "${DST_USER}" || true )\
    && chown -R "${DST_USER}:${DST_GROUP}" "${DST_USER_DATA_PATH}"

# install steamcmd only if steamcmd doesn't exist
RUN test -e "${STEAMCMD_PATH}" && echo "Steamcmd detected" \
    || ( \
    echo "Installing steamcmd" \
    && mkdir -p /opt/steamcmd \
    && wget "${STEAMCMD_URL}" -O /tmp/steamcmd.tar.gz \
    && tar -xvzf /tmp/steamcmd.tar.gz -C /opt/steamcmd \
    && rm -rf /tmp/* \
    )

# install helper tools
COPY supervisor /etc/supervisor/
COPY scripts_system /usr/local/bin/
COPY scripts_steam /opt/steamcmd_scripts/
RUN chmod +x /usr/local/bin/*

# install Don't Starve Together server
RUN mkdir -p /opt/dst_server \
    && chown -R "${DST_USER}:${DST_GROUP}" /opt/dst_server \
    && if [ "${DST_DOWNLOAD}" = "1" ]; then \
        installed=0; \
        for attempt in 1 2 3; do \
            if steamcmd +runscript /opt/steamcmd_scripts/install_dst_server_initial; then installed=1; break; fi; \
            echo "DST server download attempt ${attempt} failed" >&2; \
            if [ "${attempt}" -lt 3 ]; then sleep 5; fi; \
        done; \
        if [ "${installed}" -ne 1 ] || ! test -d /opt/dst_server/bin64 || ! test -d /opt/dst_server/bin; then \
            echo "ERROR: DST server download failed" >&2; exit 1; \
        fi; \
    else echo "Skipping DST server download"; fi \
    && rm -rf /root/Steam /root/.steam

# install default config
COPY dst_default_config /opt/dst_default_config/
RUN chown -R "${DST_USER}:${DST_GROUP}" /opt/dst_default_config

# pass essential environment variables into the container
ENV STEAMCMD_PATH=$STEAMCMD_PATH
ENV DST_USER=$DST_USER
ENV DST_GROUP=$DST_GROUP
ENV DST_USER_DATA_PATH=$DST_USER_DATA_PATH
VOLUME [ "${DST_USER_DATA_PATH}" ]

EXPOSE 10999-11000/udp 12346-12347/udp
ENTRYPOINT [ "entrypoint.sh" ]
CMD ["supervisord", "-c", "/etc/supervisor/supervisor.conf", "-n"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD [ "healthcheck.sh" ]
