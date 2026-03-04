FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

# OpenSCAD + Inkscape + potrace for SVG->STL and bitmap->SVG
# OpenCV for background removal + preprocessing
RUN apt-get update && apt-get install -y \
  python3 python3-pip git \
  inkscape openscad potrace imagemagick \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone https://github.com/Papooch/cookie-cutter-generator.git

WORKDIR /app
COPY app /app

RUN pip3 install --no-cache-dir \
  fastapi uvicorn python-multipart \
  opencv-python-headless numpy

EXPOSE 8088
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8088"]
