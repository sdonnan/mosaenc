from python:3.12-bookworm

# install deps
run apt update \
    && apt install -y \
        build-essential \
        curl \
        cmake \
        libsqlite3-dev \
        protobuf-compiler \
        libproj-dev \
        python3-dev \
        swig \
        unzip \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# install a new gdal
run curl -LJo /gdal.tar.gz https://github.com/OSGeo/gdal/releases/download/v3.8.4/gdal-3.8.4.tar.gz \
    && tar -xf /gdal.tar.gz \
    && cd gdal-3.8.4 \
    && mkdir build \
    && cd build \
    && cmake -DCMAKE_BUILD_TYPE=Release .. \
    && make install -j `nproc` \
    && ldconfig \
    && cd ../.. \
    && rm -r gdal-3.8.4 \
    && rm /gdal.tar.gz

# install tippecanoe
run curl -LJo /tippecanoe.zip https://github.com/mapbox/tippecanoe/archive/refs/heads/master.zip \
    && unzip /tippecanoe.zip \
    && cd tippecanoe-master \
    && make -j `nproc` \
    && make install \
    && cd .. \
    && rm -r tippecanoe-master \
    && rm /tippecanoe.zip

workdir /
add mosaenc.py /
add templates /templates
entrypoint ["python3", "/mosaenc.py"]