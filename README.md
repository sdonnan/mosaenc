Mosaenc
=======

Generates Mapbox Tiles (mbtiles) from NOAA ENC maps.
Pronounced like "Mosaic".

This is a work in progress.
It is not meant for production.

Usage
-----

It is probably easiest to use containers to run software.
The following will build a map of the Gulf of Mexico and then host it.

```
$ docker build --tag mosaenc .
$ docker build --tag tileserver tileserver
$ mkdir build
$ docker run -it --rm -v(pwd)/build:/build mosaenc --verbose --get --geojson --tile --style --bb -98 -88 22 3
$ docker run -it --rm -p8080:8080 -v(pwd)/build/mbtiles:/data tileserver
```

To Do
-----

Currently the output styles assume there are charts at each band for the area you want to map.
This is a bad assumption.
This script should be better about filling in the missing zoom levels with the available data.