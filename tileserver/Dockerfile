from ubuntu:22.04

# install deps
run apt update \
    && apt install -y \
        curl \
        unzip \
    && apt install -y \
        build-essential \
        gir1.2-rsvg-2.0 \
        libcairo2-dev \
        libcurl4-openssl-dev \
        libgif-dev \
        libglfw3-dev \
        libicu70 \
        libjpeg-dev \
        libjpeg-turbo8 \
        libpango1.0-dev \
        libpixman-1-0 \
        libpixman-1-dev \
        librsvg2-2 \
        librsvg2-common \
        librsvg2-dev \
        libuv1-dev \
        pkg-config \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

# install nodejs
run curl -o /node.tar.xz https://nodejs.org/dist/v20.11.1/node-v20.11.1-linux-x64.tar.xz \
    && mkdir -p /usr/local/lib/nodejs \
    && tar -xJvf /node.tar.xz -C /usr/local/lib/nodejs \
    && rm /node.tar.xz

env PATH="/usr/local/lib/nodejs/node-v20.11.1-linux-x64/bin:$PATH"

# build tileserver-gl
run curl -LJo /tileserver-src.zip https://github.com/maptiler/tileserver-gl/archive/refs/tags/v4.10.2.zip \
    && unzip /tileserver-src.zip \
    && rm /tileserver-src.zip \
    && cd tileserver-gl-4.10.2 \
    && npm install

workdir /data
cmd xvfb-run --server-args="-screen 0 1024x768x24" node /tileserver-gl-4.10.2