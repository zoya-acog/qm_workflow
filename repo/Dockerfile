FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gcc \
        libc-dev \
        slurm-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY advQMcalc_test/ /srv/advQMcalc_test/
COPY app/ /srv/app/
COPY extra/slurm.conf /srv/app/slurm.conf

# Matching the working stack from the reference app (/home/zoya/AutoMD-OXtalS,
# deploy/Dockerfile.voila): ipyvuetify's widget components need the older
# notebook-6 / ipywidgets-7 classic-nbextension stack, not the newer voila 0.5 /
# ipywidgets 8 federated-labextension stack this image was on before. This
# supersedes (and removes the need for) the tornado<6.5 / ipython<9 / --template
# workarounds that were specific to bugs in that newer stack — this older
# combination doesn't hit them in the first place.
RUN pip install --no-cache-dir \
        /srv/advQMcalc_test \
        "voila>=0.4,<0.5" \
        "notebook==6.5.4" \
        "ipywidgets==7.6.5" \
        "widgetsnbextension==3.5.2" \
        "jupyterlab_widgets==1.1.4" \
        "ipyvuetify==1.8.10" \
        "ipyvue>=1.9,<2" \
        ipykernel \
        pandas \
    && jupyter nbextension install --sys-prefix --py ipyvuetify --overwrite \
    && jupyter nbextension enable  --sys-prefix --py ipyvuetify \
    && jupyter nbextension install --sys-prefix --py widgetsnbextension --overwrite \
    && jupyter nbextension enable  --sys-prefix --py widgetsnbextension \
    && jupyter serverextension enable --sys-prefix voila \
    && python -m ipykernel install --name python3 --display-name "Python 3"

RUN pip install --no-cache-dir cif2cell

RUN chmod -R 777 /srv

ENV SLURM_CONF=/srv/app/slurm.conf

CMD ["voila", "--port=8866", "--Voila.ip=0.0.0.0", "--no-browser", "--autoreload=False", "app/app.ipynb"]
