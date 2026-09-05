# Tango, YouTube transcripts to Anki flashcards.
#
# Built for the "just run it" case: no Python setup, no virtualenv, no spaCy
# download step. One image, one command.
#
#   docker build -t tango .
#   docker run --rm -v "$PWD/out:/data/output" tango run <video-id> --deck "French"
#
# Two things are deliberate and worth reading before changing them.
#
# The English spaCy model is baked in, so `docker run tango run <id>` works
# with no setup at all. Other languages are not: 24 models would multiply the
# image size for something most users never ask for. Install one into a
# mounted volume with `docker run ... tango install-model fr`, or bake it in
# with a derived image.
#
# Nothing writes inside the container by default. /data holds the definition
# cache, the indexes and the generated packages, and it is a volume. Without
# mounting it, every run starts with an empty cache and the .apkg disappears
# when the container exits, which is almost never what anyone wants.

FROM python:3.10-slim

# tini reaps zombies and forwards signals, so ctrl-c during a long definition
# phase stops the run instead of detaching it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

# Build from the published package rather than the source tree: it is the
# thing users actually install, so the image tests the same artifact.
ARG TANGO_VERSION=0.8.2
RUN pip install --no-cache-dir "tango-anki==${TANGO_VERSION}" \
 && python -m spacy download en_core_web_sm

# All state lives here, and here alone.
ENV DB_PATH=/data/pipeline.db \
    DICT_DIR=/data/dictionaries \
    IMAGE_DIR=/data/images \
    MEDIA_DIR=/data/media \
    OUTPUT_DIR=/data/output \
    REVIEW_FILE=/data/review.json
RUN mkdir -p /data/dictionaries /data/images /data/media /data/output
VOLUME ["/data"]

# AnkiConnect runs on the host, not in here. On Docker Desktop the host is
# host.docker.internal; on native Linux, run with --network host and leave
# this unset, or pass --add-host=host.docker.internal:host-gateway.
ENV ANKI_HOST=http://host.docker.internal:8765

# Non-root, because nothing here needs root and a container that writes to a
# mounted volume as root leaves files the host user cannot delete.
RUN useradd --create-home --uid 1000 tango \
 && chown -R tango:tango /data
USER tango

WORKDIR /data
ENTRYPOINT ["/usr/bin/tini", "--", "tango"]
CMD ["--help"]
