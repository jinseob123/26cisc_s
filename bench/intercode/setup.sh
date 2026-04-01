#!/bin/bash

# Create docker images for bash, sql environments
echo "Setting up docker image for bash..."
docker build -t intercode-bash -f docker/bash.Dockerfile .

echo "Setting up docker image for nl2bash..."
for version in 1 2 3 4; do
  docker build \
    --build-arg file_system_version="$version" \
    -t "intercode-nl2bash-fs${version}" \
    -f docker/nl2bash.Dockerfile .
done

# Keep the legacy tag pointing at fs1 for backwards compatibility.
docker tag intercode-nl2bash-fs1 intercode-nl2bash

echo "Setting up docker image for sql..."
docker-compose -f docker/sql-docker-compose.yml up -d

echo "Setting up docker image for python..."
docker build -t intercode-python -f docker/python.Dockerfile .

echo "Setting up docker images for ctf..."
docker build -t intercode-ctf -f docker/ctf.Dockerfile .

echo "Setting up docker images for swe-bench..."
docker build -t intercode-swe -f docker/swe.Dockerfile .
