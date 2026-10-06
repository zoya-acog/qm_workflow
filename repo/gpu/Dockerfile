# Quantum ESPRESSO GPU build covering RTX 4090 (cc89) and RTX 5080 (cc120).
# NVIDIA's prebuilt hpc/quantum_espresso:qe-7.3.1 lacks cc89/cc120 kernels.
FROM nvcr.io/nvidia/nvhpc:25.7-devel-cuda12.9-ubuntu22.04

ARG QE_VERSION=7.3.1
ENV NVHPC_ROOT=/opt/nvidia/hpc_sdk/Linux_x86_64/25.7
ENV PATH=${NVHPC_ROOT}/comm_libs/mpi/bin:${NVHPC_ROOT}/compilers/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl -sSL https://gitlab.com/QEF/q-e/-/archive/qe-${QE_VERSION}/q-e-qe-${QE_VERSION}.tar.gz \
    | tar xz && mv q-e-qe-${QE_VERSION} q-e

WORKDIR /build/q-e
RUN ./configure --prefix=/opt/qe \
      --with-cuda=${NVHPC_ROOT}/cuda --with-cuda-runtime=12.9 --with-cuda-cc=89 \
      --with-cuda-mpi=yes --enable-openmp --with-scalapack=no \
    && sed -i 's/-gpu=cc89/-gpu=cc89,cc120/g' make.inc \
    && grep -n "gpu=" make.inc | head -3 \
    && make -j8 pw \
    && make install \
    && cd / && rm -rf /build

ENV PATH=/opt/qe/bin:${PATH}
ENV OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
