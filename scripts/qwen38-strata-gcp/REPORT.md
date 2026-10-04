# Qwen3.8-Flash-Next Strata Deployment Report

## Conclusion

The deployment passed. `Qwen3.8-Flash-Next` served a successful
OpenAI-compatible chat completion while Strata stayed below a 12 GB VRAM
budget on a host with 64 GB system RAM.

This is not a smaller base model. It is the ISTA-DASLab GSQ-RCO `IQ2_XS`
quantization of the full Qwen3.8-Flash-Next MoE model. Strata makes the low
VRAM configuration possible by keeping about 33 GiB of experts in system RAM
and caching only a selected portion on the GPU.

## Test environment

Test date: 2026-10-04 (Asia/Taipei)

| Item | Value |
| --- | --- |
| Cloud | Google Cloud project `gen-lang-client-0321260816` (`compal`) |
| VM | `qwen38-strata-test`, `g2-standard-16`, `us-central1-b` |
| CPU | 16 vCPU, Intel Xeon 2.20 GHz, 8 cores / 16 threads |
| RAM | 62.8 GiB visible (64 GB configured), no swap |
| GPU | NVIDIA L4, 23,034 MiB, compute capability 8.9 |
| Driver | 580.178.04, CUDA runtime 13.0 |
| OS | Ubuntu 22.04.5 LTS |
| Disk | 200 GB `pd-ssd` boot disk |
| Docker | 29.1.3 |
| Strata commit | `99f3dbd0b21d1401b3769e0c0d963913607f380b` |
| Model quant | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`, `IQ2_XS` |
| Context | 32,768 tokens |
| KV cache | INT8 |
| Vision | Disabled |

## Storage

The two model shards occupied 68,026,093,024 bytes in total:

- Shard 1: 39,225,954,592 bytes
- Shard 2: 28,800,138,432 bytes
- MTP speculative-decoding data: about 6.5 GiB

A 100 GB free-disk minimum is practical. A 200 GB disk leaves comfortable
space for the Docker image, build layers, model, logs, and updates.

## Resource measurements

The unrestricted L4 baseline used almost the entire 24 GB card:

| Mode | Strata process VRAM | Total GPU used | GPU free | Host RAM used |
| --- | ---: | ---: | ---: | ---: |
| Automatic cache | 22,032 MiB | 22,107 MiB | 458 MiB | 36 GiB |
| 12 GB budget emulation | 10,444 MiB | 10,519 MiB | 12,046 MiB | 36 GiB |

The constrained run used:

```text
--vram-reserve-mib 12288
```

Strata cached 3,672 experts and reported 4.94 GiB for the GPU expert cache.
It loaded about 33.02 GiB of experts into RAM. The API health check returned
`status: ok` after the constrained reload.

This demonstrates that 12 GB VRAM plus 64 GB RAM is sufficient for this
specific quant, context, and single-request test. It does not claim the same
throughput under high concurrency, very long active contexts, or vision use.

## Inference result

Request:

```text
請用繁體中文回答：列出三點量化能降低大型語言模型部署資源需求的原因，每點一句。
```

Observed result:

- HTTP status: 200
- Prompt tokens: 36
- Completion tokens: 95
- Total tokens: 131
- End-to-end time: 4.214 seconds
- Prompt processing: 35.9 token/s
- Generation: 29.6 token/s
- MTP draft tokens: 64; accepted: 43
- Finish reason: `stop`

The response was coherent Traditional Chinese and gave three distinct reasons:
smaller weights, lower numerical-computation cost, and operation on lower-cost
edge or cloud hardware.

## Engine and portability

This GSQ-RCO GGUF path uses Strata. It is not a drop-in vLLM model: vLLM does
not directly implement Strata's GSQ-RCO expert paging/cache path for these
weights. A different Qwen checkpoint and supported quant format would be
needed for a vLLM deployment.

The tested image was compiled for CUDA architecture 8.9. For migration:

- NVIDIA L4 or L20: build for architecture `89`.
- RTX A6000: build for architecture `86`.
- A reusable image can be built with `CUDA_ARCHITECTURES=86;89`.

Both L20 and RTX A6000 provide more VRAM than the proof requires. Use
`set-vram-reserve.sh` only when deliberately reproducing the 12 GB limit;
otherwise allow Strata to use the extra VRAM for a larger expert cache and
better throughput.

## Service exposure

The test endpoint was bound to `127.0.0.1:8080` and no GCP HTTP/HTTPS firewall
rule was enabled. Therefore, no public or fixed URL was created. This was
intentional because the local test instance had no API key.

For production, use a reserved IP plus DNS, TLS reverse proxy or authenticated
tunnel, and an API key. Keep port 8080 private. The application-facing API is:

```text
POST /v1/chat/completions
GET  /v1/models
GET  /health
```

## Rebuild sequence

```bash
bash install-host.sh
sudo reboot
bash build-strata.sh
bash run-strata.sh
sudo docker logs -f strata-qwen38
RESERVE_MIB=12288 bash set-vram-reserve.sh  # only for 24 GB emulation
bash test-api.sh
```

The Docker volume `strata-data` preserves downloaded model files and generated
configuration across container recreation. Deleting the VM and boot disk also
deletes this local cache, but all scripts and exact tested versions remain in
this directory for a clean rebuild.

The reusable engine image is published separately from the weights at:

```text
ghcr.io/zhyixi/qwen38-strata:cuda13-sm86-sm89
```

The image contains Strata and kernels for compute capabilities 8.6 and 8.9.
It intentionally does not contain the roughly 75 GB of model and MTP data;
those files are downloaded to the `strata-data` volume on first start.

## Registry verification

GitHub Actions run `37183742583` completed successfully. An anonymous Docker
configuration could read the public manifest, and the GCP test host then
performed a real pull of the image:

```text
index digest: sha256:7bbf0b3b4d049e0024020eedc26df4f4757836c589e22cf9543007701d76247d
platform: linux/amd64
```

A new container created from the GHCR image mounted the existing
`strata-data` volume, found the downloaded model without downloading it again,
and started with the saved 12 GB VRAM constraint. Final verification returned:

- Health: `status: ok`
- Strata process VRAM: 10,444 MiB
- Chat response: `GHCR 啟動成功`
- Warm generation speed: 22.9 token/s for the short six-token response

## Cloud cleanup

After the report and reusable image were published, the temporary GCP test
environment was removed. The console was checked after deletion and showed:

- No VM instances
- No persistent disks
- No snapshots
- No snapshot schedules
- No reserved internal or external IP addresses

The deleted VM, GPU, CPU, RAM, and 200 GB SSD therefore no longer produce new
resource charges. Previously accrued usage can remain visible in billing
reports until Google's normal reporting delay has passed.
