FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

# Tools: OpenSCAD + Inkscape + potrace for image->svg
RUN apt-get update && apt-get install -y \
  python3 python3-pip git \
  inkscape openscad potrace imagemagick \
  && rm -rf /var/lib/apt/lists/*

# Pull Papooch engine
WORKDIR /opt
RUN git clone https://github.com/Papooch/cookie-cutter-generator.git

# API wrapper
WORKDIR /app
COPY app /app
RUN pip3 install --no-cache-dir fastapi uvicorn python-multipart

EXPOSE 8088
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8088"]
