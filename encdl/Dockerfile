from python:3.12-bookworm

# install deps
run apt update \
    && apt install -y \
        build-essential \
        curl \
        gdal-bin \
        libsqlite3-dev \
        protobuf-compiler \
        unzip \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# install tippecanoe
run curl -LJo /tippecanoe.zip https://github.com/mapbox/tippecanoe/archive/refs/heads/master.zip \
    && unzip /tippecanoe.zip \
    && cd tippecanoe-master \
    && make -j \
    && make install \
    && cd .. \
    && rm -r tippecanoe-master \
    && rm /tippecanoe.zip
