# CPU Quantum ESPRESSO 7.2 (same version as the host install) with Open MPI,
# so MPI runs inside one container instead of uncoordinated `srun pw.x` copies.
FROM ubuntu:22.04
ARG QE_VERSION=7.2
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        gfortran gcc make curl ca-certificates git \
        libopenmpi-dev openmpi-bin libopenblas-dev libfftw3-dev libfftw3-mpi-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -sSL https://gitlab.com/QEF/q-e/-/archive/qe-${QE_VERSION}/q-e-qe-${QE_VERSION}.tar.gz | tar xz \
    && mv q-e-qe-${QE_VERSION} q-e
WORKDIR /build/q-e
RUN ./configure --prefix=/opt/qe --enable-openmp=no --with-scalapack=no \
    && make -j8 pw \
    && make install \
    && cd / && rm -rf /build
ENV PATH=/opt/qe/bin:${PATH}
ENV OMPI_MCA_btl_vader_single_copy_mechanism=none OMPI_MCA_rmaps_base_oversubscribe=1
