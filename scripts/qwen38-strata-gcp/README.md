# Qwen3.8-Flash-Next with Strata

This directory preserves the deployment path that was verified on 2026-10-04.
It runs the ISTA-DASLab `IQ2_XS` GSQ-RCO GGUF build of
`Qwen/Qwen3.8-Flash-Next` with the Strata inference engine.

## Verified target

- 64 GB system RAM
- About 12 GB usable VRAM
- Ubuntu 22.04 x86_64
- NVIDIA driver 580 or newer
- Docker with NVIDIA Container Toolkit
- At least 100 GB free local disk; 200 GB is recommended

The cloud verification used one NVIDIA L4 (24 GB) and configured Strata with
`--vram-reserve-mib 12288`. This left 12,046 MiB free and limited the Strata
process to 10,444 MiB, which demonstrates operation inside a 12 GB VRAM budget.
See [REPORT.md](REPORT.md) for the measured results.

## Quick start

Run these commands on a new Ubuntu GPU host:

```bash
cd qwen38-strata-gcp
bash install-host.sh
sudo reboot
```

After reconnecting, pull the prebuilt multi-GPU image and start it:

```bash
cd qwen38-strata-gcp
bash run-strata.sh
```

`run-strata.sh` defaults to
`ghcr.io/zhyixi/qwen38-strata:cuda13-sm86-sm89`. To build locally instead:

```bash
bash build-strata.sh
STRATA_IMAGE=strata:qwen38 bash run-strata.sh
```

The first start compiles the Strata kernels and downloads roughly 68 GB of
main GGUF weights plus about 6.5 GB of MTP data. Follow progress with:

```bash
sudo docker logs -f strata-qwen38
```

On a physical 12 GB GPU, Strata's automatic cache sizing should use the
available card memory. To emulate a 12 GB card on a 24 GB or 48 GB GPU after
the initial setup has generated its JSON config:

```bash
RESERVE_MIB=12288 bash set-vram-reserve.sh  # 24 GB L4
RESERVE_MIB=36864 bash set-vram-reserve.sh  # 48 GB L20/A6000
```

Then test the local OpenAI-compatible API:

```bash
bash test-api.sh
```

## GPU portability

The published image and `build-strata.sh` include CUDA architectures 8.6 and
8.9 by default:

- RTX A6000: compute capability 8.6
- NVIDIA L4 and L20: compute capability 8.9

For a smaller host-specific image, set `CUDA_ARCHITECTURES=86` or
`CUDA_ARCHITECTURES=89` before building.

VRAM size and CUDA architecture are separate concerns. A 10 GB or 12 GB
Ampere/Ada card can use the same image; Strata automatically reduces the GPU
expert cache and uses more of the 64 GB host RAM. The verified floor is about
12 GB VRAM. Cards below that may work but are not claimed as verified by this
report and should be tested with `test-api.sh` before production use.

## Network safety

The default command publishes the API only on `127.0.0.1:8080`. It does not
create a public URL or firewall rule. For a real external service, keep this
loopback binding and put a TLS reverse proxy or authenticated tunnel in front
of it. Do not expose Strata directly without an API key.

## GCP recreation reference

The verified VM used:

- Project: `gen-lang-client-0321260816` (`compal`)
- Zone: `us-central1-b`
- Machine: `g2-standard-16`
- Accelerator: one NVIDIA L4
- Boot disk: 200 GB `pd-ssd`
- Image: Ubuntu 22.04 LTS

Equivalent `gcloud` creation command:

```bash
gcloud compute instances create qwen38-strata-test \
  --project=gen-lang-client-0321260816 \
  --zone=us-central1-b \
  --machine-type=g2-standard-16 \
  --accelerator=type=nvidia-l4,count=1 \
  --maintenance-policy=TERMINATE \
  --restart-on-failure \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=200GB \
  --boot-disk-type=pd-ssd
```

Creating this VM starts billable resources. Delete the VM, its boot disk, and
any snapshots when the test is finished.
