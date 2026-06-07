import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from difflib import SequenceMatcher
from typing import Any, TypedDict

import httpx
import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from PIL import Image, ImageDraw
from tqdm import tqdm

sys.path.extend([".", ".."])

from containers import Container
from fast_api_service.main import app


DEFAULT_OUTPUT_DIR = "/app/tests/reports"
DEFAULT_DATASET_PATH = "/app/tests/data/iap_ae_v3_eval_dataset.jsonl"
DEFAULT_GRAPH_PATH = "/app/tests/reports/iap_ae_v3_rag_eval_graph.png"
RETRIEVE_URL = "/api/v1/ae/retrieve"


class EvalGraphState(TypedDict, total=False):
    args: argparse.Namespace
    container: Container
    dataset: list[dict[str, Any]]
    report_rows: list[dict[str, Any]]
    output_path: str
    summary: dict[str, Any]
    graph_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="手動執行 AE FACA v3 RAG 檢索準確度評估，並輸出 xlsx 報告。"
    )
    parser.add_argument("--samples", type=int, default=10, help="從每個 index 抽樣的原文數量。")
    parser.add_argument("--questions-per-doc", type=int, default=2, help="每筆原文產生的問題數。")
    parser.add_argument("--top-k", type=int, default=5, help="計算 Hit@K 的 K 值。")
    parser.add_argument("--syslang", default="ZH", help="呼叫 retrieve API 的 syslang。")
    parser.add_argument(
        "--indices",
        default="iap_ae_issue_zh, file_zh, ae_sop_file_zh",
        help="逗號分隔的 ES index，例如 iap_ae_issue_zh,file_zh。",
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="評估資料集 jsonl 路徑。")
    parser.add_argument(
        "--reuse-dataset",
        action="store_true",
        help="重用既有 dataset，不重新從 ES 抽樣與產生問題。",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="只跑檢索指標，不使用 LLM 評估答案品質。",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="xlsx 報告輸出資料夾。")
    parser.add_argument("--graph-path", default=DEFAULT_GRAPH_PATH, help="LangGraph 流程圖 PNG 輸出路徑。")
    parser.add_argument("--timeout", type=float, default=300.0, help="單次 API timeout 秒數。")
    parser.add_argument("--seed", type=int, default=42, help="ES random_score seed。")
    parser.add_argument("--max-cases", type=int, default=0, help="限制本次最多評估幾筆；0 代表不限制。")
    return parser.parse_args()


def llm_to_text(llm: Any, prompt: str, system_prompt: str = "") -> str:
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))

    if hasattr(llm, "invoke"):
        response = llm.invoke(messages)
        return str(getattr(response, "content", response))
    if hasattr(llm, "chat"):
        return str(llm.chat(usr_input=prompt, system_prompt=system_prompt))
    raise TypeError(f"Unsupported LLM object: {type(llm)}")


def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def extract_json(text: str, fallback: Any) -> Any:
    clean = strip_thinking(text)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", clean, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        clean = fenced.group(1).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    first_array = re.search(r"\[.*\]", clean, flags=re.DOTALL)
    first_obj = re.search(r"\{.*\}", clean, flags=re.DOTALL)
    for match in [first_array, first_obj]:
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
    return fallback


def build_dummy_issue_id(source: dict[str, Any], text: str) -> str:
    metadata = source.get("metadata") or {}
    raw_source = (
        metadata.get("file_path")
        or metadata.get("file_name")
        or metadata.get("id")
        or text
    )
    digest = hashlib.sha1(str(raw_source).encode("utf-8")).hexdigest()[:10].upper()
    return f"SOP_FILE_{digest}"


def get_source_text(source: dict[str, Any]) -> str:
    metadata = source.get("metadata") or {}
    candidates = [
        source.get("text"),
        source.get("content"),
        metadata.get("rewrite_content"),
        metadata.get("raw_content"),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate).strip()
    return ""


def get_expected_issue_id(source: dict[str, Any], source_text: str) -> str:
    metadata = source.get("metadata") or {}
    basic_information = metadata.get("basic_information") or {}
    issue_id = basic_information.get("ISSUE_ID")
    if issue_id is not None and str(issue_id).strip() not in {"", "Not found Issue ID"}:
        return str(issue_id).strip()
    return build_dummy_issue_id(source, source_text)


def get_source_type(index_name: str, source: dict[str, Any]) -> str:
    node_type = str((source.get("metadata") or {}).get("node_type") or "")
    if "ae_sop_file" in index_name:
        return "SOP_FILE"
    if "file" in index_name or "file" in node_type:
        return "DEVICE_MANUAL"
    return "FACA_CASE"


def summarize_source(source: dict[str, Any]) -> dict[str, Any]:
    metadata = source.get("metadata") or {}
    basic_information = metadata.get("basic_information") or {}
    return {
        "metadata_id": metadata.get("id", ""),
        "source_doc_id": metadata.get("id", ""),
        "node_type": metadata.get("node_type", ""),
        "file_name": metadata.get("file_name", ""),
        "page_label": metadata.get("page_label", ""),
        "raw_issue_id": basic_information.get("ISSUE_ID", ""),
    }


def generate_questions(llm: Any, source_text: str, questions_per_doc: int) -> list[dict[str, str]]:
    prompt = f"""
你是一位資深設備維修知識庫評估專家。請根據下方原始文本，產生 {questions_per_doc} 個使用者可能會問的問題，用於測試 RAG 檢索。

要求：
1. 問題必須能由原始文本回答或高度相關。
2. 問法要多樣，包含精確問法、口語問法、現象描述問法。
3. 不要直接複製整段原文。
4. 嚴格輸出 JSON array，不要加 Markdown。

JSON 格式：
[
  {{"query_type": "精確提問", "question": "..."}},
  {{"query_type": "口語提問", "question": "..."}}
]

原始文本：
{source_text[:4000]}
"""
    response = llm_to_text(
        llm,
        prompt,
        system_prompt="你只輸出可被 json.loads 解析的 JSON。",
    )
    parsed = extract_json(response, fallback=[])
    questions = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if question:
                questions.append(
                    {
                        "query_type": str(item.get("query_type") or "LLM生成問題"),
                        "question": question,
                    }
                )
    if questions:
        return questions[:questions_per_doc]
    res = [
        {"query_type": "fallback", "question": "這筆維修紀錄描述的異常原因與處理方式是什麼？"}
    ][:questions_per_doc]
    return res


def judge_answer(
    llm: Any,
    question: str,
    source_text: str,
    chat_response: str,
    retrieved_context: str,
) -> dict[str, Any]:
    prompt = f"""
你是 RAG 系統評估員。請比較「原始標準文本」、「使用者問題」、「API 回答」與「API 檢索內容」，評估是否檢索正確且回答可信。

請只輸出 JSON，不要 Markdown。分數範圍 0 到 1。

輸出格式：
{{
  "retrieval_correct": true,
  "answer_correct": true,
  "relevance_score": 0.0,
  "groundedness_score": 0.0,
  "confidence": 0.0,
  "reason": "簡短原因"
}}

原始標準文本：
{source_text[:3500]}

使用者問題：
{question}

API 回答：
{chat_response[:3500]}

API 檢索內容：
{retrieved_context[:3500]}
"""
    response = llm_to_text(
        llm,
        prompt,
        system_prompt="你是嚴格的 RAG 評估員，只輸出 JSON。",
    )
    parsed = extract_json(response, fallback={})
    if not isinstance(parsed, dict):
        parsed = {}
    
    res = {
        "retrieval_correct": bool(parsed.get("retrieval_correct", False)),
        "answer_correct": bool(parsed.get("answer_correct", False)),
        "relevance_score": safe_float(parsed.get("relevance_score")),
        "groundedness_score": safe_float(parsed.get("groundedness_score")),
        "confidence": safe_float(parsed.get("confidence")),
        "judge_reason": str(parsed.get("reason") or ""),
        "judge_raw": strip_thinking(response),
    }
    return res


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def flatten_result_context(result: dict[str, Any]) -> str:
    parts = []
    text = result.get("Text", [])
    if isinstance(text, list):
        parts.extend(str(item) for item in text)
    elif text:
        parts.append(str(text))

    related_file = result.get("relatedFile", {})
    if isinstance(related_file, dict):
        for cause in related_file.values():
            if not isinstance(cause, dict):
                continue
            description = cause.get("DESCRIPTION")
            if description:
                parts.append(str(description))
            for file_key in ["file_0", "file_1", "file_2"]:
                file_info = cause.get(file_key, {})
                if not isinstance(file_info, dict):
                    continue
                for hit in file_info.get("hits") or []:
                    if isinstance(hit, dict) and hit.get("content"):
                        parts.append(str(hit["content"]))
    return "\n".join(parts)


def result_identifiers(result: dict[str, Any]) -> set[str]:
    identifiers = {
        str(result.get("ISSUE_ID", "")),
        str(result.get("source_doc_id", "")),
    }
    identifiers.update(str(item) for item in result.get("chunk_ids", []) if item)
    return {item for item in identifiers if item}


def context_overlap(source_text: str, retrieved_context: str) -> float:
    source = re.sub(r"\s+", "", source_text or "")[:2000]
    retrieved = re.sub(r"\s+", "", retrieved_context or "")[:4000]
    if not source or not retrieved:
        return 0.0
    return SequenceMatcher(None, source, retrieved).ratio()


async def sample_source_documents(
    es_client: Any,
    indices: list[str],
    samples: int,
    seed: int,
) -> list[dict[str, Any]]:
    sampled = []
    for index_name in indices:
        body = {
            "query": {
                "function_score": {
                    "query": {"exists": {"field": "text"}},
                    "random_score": {"seed": seed, "field": "_seq_no"},
                }
            },
            "_source": ["text", "content", "metadata"],
            "size": samples,
        }
        response = es_client.search(index=index_name, body=body)
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source") or {}
            source_text = get_source_text(source)
            if not source_text:
                continue
            sampled.append(
                {
                    "index_name": index_name,
                    "es_id": hit.get("_id", ""),
                    "source_type": get_source_type(index_name, source),
                    "expected_issue_id": get_expected_issue_id(source, source_text),
                    "source_text": source_text,
                    **summarize_source(source),
                }
            )
    return sampled


def load_dataset(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def save_dataset(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def build_dataset(args: argparse.Namespace, container: Container) -> list[dict[str, Any]]:
    if args.reuse_dataset and os.path.exists(args.dataset):
        return load_dataset(args.dataset)

    es_client = container.es_client()
    llm = container.vllm_general_llm()
    indices = [item.strip() for item in args.indices.split(",") if item.strip()]
    source_docs = await sample_source_documents(es_client, indices, args.samples, args.seed)

    dataset = []
    for doc in tqdm(source_docs, desc="產生評估問題"):
        questions = generate_questions(llm, doc["source_text"], args.questions_per_doc)
        for question in questions:
            dataset.append(
                {
                    **doc,
                    "query_type": question["query_type"],
                    "question": question["question"],
                }
            )

    save_dataset(args.dataset, dataset)
    return dataset


async def call_retrieve_api(
    client: httpx.AsyncClient,
    question: str,
    syslang: str,
    timeout: float,
) -> tuple[dict[str, Any], float, str]:
    payload = {
        "role": "rag-eval",
        "content": question,
        "session_id": f"iap_ae-v3-eval-{uuid.uuid4()}",
        "syslang": syslang,
    }
    start = time.perf_counter()
    response = await client.post(RETRIEVE_URL, json=payload, timeout=timeout)
    latency = time.perf_counter() - start
    response.raise_for_status()
    return response.json(), latency, json.dumps(payload, ensure_ascii=False)


async def evaluate_dataset(args: argparse.Namespace, dataset: list[dict[str, Any]], container: Container) -> list[dict[str, Any]]:
    judge_llm = None if args.no_judge else container.vllm_general_llm()
    report_rows = []
    transport = httpx.ASGITransport(app=app)
    eval_dataset = dataset[: args.max_cases] if args.max_cases else dataset

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for case_no, case in enumerate(tqdm(eval_dataset, desc="呼叫 AE FACA v3 retrieve"), start=1):
            row = {
                "case_no": case_no,
                "index_name": case.get("index_name", ""),
                "source_type": case.get("source_type", ""),
                "expected_issue_id": case.get("expected_issue_id", ""),
                "expected_source_id": case.get("source_doc_id") or case.get("metadata_id") or "",
                "es_id": case.get("es_id", ""),
                "metadata_id": case.get("metadata_id", ""),
                "source_doc_id": case.get("source_doc_id", ""),
                "node_type": case.get("node_type", ""),
                "file_name": case.get("file_name", ""),
                "page_label": case.get("page_label", ""),
                "query_type": case.get("query_type", ""),
                "question": case.get("question", ""),
                "source_text": case.get("source_text", ""),
            }
            try:
                api_json, latency, payload_json = await call_retrieve_api(
                    client,
                    question=row["question"],
                    syslang=args.syslang,
                    timeout=args.timeout,
                )
                results = api_json.get("results") or []
                expected_issue_id = row["expected_issue_id"]
                expected_source_id = row["expected_source_id"]
                expected_ids = {expected_issue_id}
                if expected_source_id and row["source_type"] in {"DEVICE_MANUAL", "SOP_FILE"}:
                    expected_ids.add(expected_source_id)
                retrieved_issue_ids = [str(item.get("ISSUE_ID", "")) for item in results]
                retrieved_source_ids = [str(item.get("source_doc_id", "")) for item in results]
                hit_rank = next(
                    (
                        idx + 1
                        for idx, item in enumerate(results)
                        if result_identifiers(item) & expected_ids
                    ),
                    0,
                )
                top_contexts = [flatten_result_context(item) for item in results[: args.top_k]]
                retrieved_context = "\n\n".join(top_contexts)

                row.update(
                    {
                        "status": "ok",
                        "latency_sec": round(latency, 3),
                        "hit_rank": hit_rank,
                        f"hit_at_{args.top_k}": int(0 < hit_rank <= args.top_k),
                        "hit_at_1": int(hit_rank == 1),
                        "mrr": round(1 / hit_rank, 4) if hit_rank else 0.0,
                        "context_overlap": round(context_overlap(row["source_text"], retrieved_context), 4),
                        "chat_response": api_json.get("chat_response", ""),
                        "top_issue_ids": " | ".join(retrieved_issue_ids[: args.top_k]),
                        "top_source_doc_ids": " | ".join(retrieved_source_ids[: args.top_k]),
                        "top_scores": " | ".join(str(item.get("Score", "")) for item in results[: args.top_k]),
                        "top_context": retrieved_context[:6000],
                        "source": json.dumps(api_json.get("source", []), ensure_ascii=False),
                        "suggested_questions": json.dumps(api_json.get("suggested_questions", []), ensure_ascii=False),
                        "request_payload": payload_json,
                    }
                )

                for idx, item in enumerate(results[: args.top_k], start=1):
                    row[f"top{idx}_issue_id"] = item.get("ISSUE_ID", "")
                    row[f"top{idx}_source_doc_id"] = item.get("source_doc_id", "")
                    row[f"top{idx}_chunk_ids"] = json.dumps(item.get("chunk_ids", []), ensure_ascii=False)
                    row[f"top{idx}_score"] = item.get("Score", "")
                    row[f"top{idx}_text"] = flatten_result_context(item)[:1500]

                if judge_llm is not None:
                    row.update(
                        judge_answer(
                            judge_llm,
                            question=row["question"],
                            source_text=row["source_text"],
                            chat_response=row["chat_response"],
                            retrieved_context=row["top_context"],
                        )
                    )
            except Exception as exc:
                row.update(
                    {
                        "status": "error",
                        "error": repr(exc),
                        "latency_sec": 0,
                        "hit_rank": 0,
                        f"hit_at_{args.top_k}": 0,
                        "hit_at_1": 0,
                        "mrr": 0.0,
                        "context_overlap": 0.0,
                    }
                )
            report_rows.append(row)
    return report_rows


def build_summary(report_df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    ok_df = report_df[report_df["status"] == "ok"] if not report_df.empty else report_df
    total_cases = len(report_df)
    ok_cases = len(ok_df)
    summary = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_cases": total_cases,
        "ok_cases": ok_cases,
        "error_cases": total_cases - ok_cases,
        "hit_at_1": safe_float(ok_df["hit_at_1"].mean()) if ok_cases else 0.0,
        f"hit_at_{args.top_k}": safe_float(ok_df[f"hit_at_{args.top_k}"].mean()) if ok_cases else 0.0,
        "mrr": safe_float(ok_df["mrr"].mean()) if ok_cases else 0.0,
        "avg_context_overlap": safe_float(ok_df["context_overlap"].mean()) if ok_cases else 0.0,
        "avg_latency_sec": safe_float(ok_df["latency_sec"].mean()) if ok_cases else 0.0,
    }

    for column in ["retrieval_correct", "answer_correct", "relevance_score", "groundedness_score", "confidence"]:
        if column in ok_df.columns:
            summary[f"avg_{column}"] = safe_float(ok_df[column].mean()) if ok_cases else 0.0

    return pd.DataFrame([summary])


def write_report(
    output_dir: str,
    report_rows: list[dict[str, Any]],
    dataset: list[dict[str, Any]],
    args: argparse.Namespace,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"iap_ae_v3_rag_eval_{timestamp}.xlsx")

    report_df = pd.DataFrame(report_rows)
    dataset_df = pd.DataFrame(dataset)
    summary_df = build_summary(report_df, args)
    failure_df = report_df[
        (report_df.get("status") != "ok")
        | (report_df.get(f"hit_at_{args.top_k}", 0) == 0)
        | (report_df.get("answer_correct", True) == False)
    ].copy()
    config_df = pd.DataFrame(
        [
            {
                "samples": args.samples,
                "questions_per_doc": args.questions_per_doc,
                "top_k": args.top_k,
                "syslang": args.syslang,
                "indices": args.indices,
                "dataset": args.dataset,
                "reuse_dataset": args.reuse_dataset,
                "no_judge": args.no_judge,
                "seed": args.seed,
                "max_cases": args.max_cases,
            }
        ]
    )

    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        report_df.to_excel(writer, sheet_name="cases", index=False)
        failure_df.to_excel(writer, sheet_name="failures", index=False)
        dataset_df.to_excel(writer, sheet_name="dataset", index=False)
        config_df.to_excel(writer, sheet_name="config", index=False)

        workbook = writer.book
        wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
        for sheet_name in ["cases", "failures", "dataset"]:
            worksheet = writer.sheets.get(sheet_name)
            if worksheet:
                worksheet.set_column(0, 12, 18)
                worksheet.set_column(13, 40, 45, wrap)
        writer.sheets["summary"].set_column(0, len(summary_df.columns), 20)

    return output_path


async def init_context_node(state: EvalGraphState) -> dict[str, Any]:
    args = state["args"]
    container = Container()
    container.wire(modules=[__name__])
    return {"container": container, "graph_path": args.graph_path}


async def build_dataset_node(state: EvalGraphState) -> dict[str, Any]:
    dataset = await build_dataset(state["args"], state["container"])
    return {"dataset": dataset}


async def validate_dataset_node(state: EvalGraphState) -> dict[str, Any]:
    dataset = state.get("dataset") or []
    if not dataset:
        raise RuntimeError("沒有建立任何評估資料，請確認 ES index 與抽樣條件。")
    return {}


async def evaluate_dataset_node(state: EvalGraphState) -> dict[str, Any]:
    report_rows = await evaluate_dataset(
        state["args"],
        state["dataset"],
        state["container"],
    )
    return {"report_rows": report_rows}


async def write_report_node(state: EvalGraphState) -> dict[str, Any]:
    args = state["args"]
    report_rows = state["report_rows"]
    dataset = state["dataset"]
    output_path = write_report(args.output_dir, report_rows, dataset, args)
    summary = build_summary(pd.DataFrame(report_rows), args).iloc[0].to_dict()
    return {"output_path": output_path, "summary": summary}


async def print_summary_node(state: EvalGraphState) -> dict[str, Any]:
    print("\nAE FACA v3 RAG 評估完成")
    print(json.dumps(state["summary"], ensure_ascii=False, indent=2))
    print(f"報告路徑: {state['output_path']}")
    print(f"資料集路徑: {state['args'].dataset}")
    print(f"流程圖路徑: {state['graph_path']}")
    return {}


def write_fallback_graph_png(graph_path: str, node_names: list[str]) -> None:
    width = 760
    box_width = 260
    box_height = 54
    gap = 30
    margin_top = 40
    height = margin_top * 2 + len(node_names) * box_height + (len(node_names) - 1) * gap
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    x0 = (width - box_width) // 2
    x1 = x0 + box_width
    for idx, node_name in enumerate(node_names):
        y0 = margin_top + idx * (box_height + gap)
        y1 = y0 + box_height
        draw.rounded_rectangle((x0, y0, x1, y1), radius=12, outline="#2563eb", width=2, fill="#eff6ff")
        text_bbox = draw.textbbox((0, 0), node_name)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        draw.text(
            (x0 + (box_width - text_width) / 2, y0 + (box_height - text_height) / 2),
            node_name,
            fill="#111827",
        )
        if idx < len(node_names) - 1:
            arrow_x = width // 2
            arrow_y0 = y1 + 4
            arrow_y1 = y1 + gap - 4
            draw.line((arrow_x, arrow_y0, arrow_x, arrow_y1), fill="#374151", width=2)
            draw.polygon(
                [
                    (arrow_x, arrow_y1 + 6),
                    (arrow_x - 6, arrow_y1 - 4),
                    (arrow_x + 6, arrow_y1 - 4),
                ],
                fill="#374151",
            )

    image.save(graph_path)


def write_graph_image(compiled_graph: Any, graph_path: str, node_names: list[str]) -> None:
    os.makedirs(os.path.dirname(graph_path), exist_ok=True)
    graph = compiled_graph.get_graph()
    try:
        with open(graph_path, "wb") as f:
            f.write(graph.draw_mermaid_png())
        logging.info("LangGraph 評估流程圖已輸出: %s", graph_path)
    except Exception as exc:
        mermaid_path = os.path.splitext(graph_path)[0] + ".mmd"
        with open(mermaid_path, "w", encoding="utf-8") as f:
            f.write(graph.draw_mermaid())
        write_fallback_graph_png(graph_path, node_names)
        logging.warning(
            "Mermaid PNG 輸出失敗，已輸出本地 PNG fallback 與 Mermaid: %s；原因: %s",
            mermaid_path,
            exc,
        )


def create_eval_graph(graph_path: str) -> Any:
    workflow = StateGraph(EvalGraphState)
    node_names = [
        "init_context",
        "build_dataset",
        "validate_dataset",
        "evaluate_dataset",
        "write_report",
        "print_summary",
    ]
    workflow.add_node(node_names[0], init_context_node)
    workflow.add_node(node_names[1], build_dataset_node)
    workflow.add_node(node_names[2], validate_dataset_node)
    workflow.add_node(node_names[3], evaluate_dataset_node)
    workflow.add_node(node_names[4], write_report_node)
    workflow.add_node(node_names[5], print_summary_node)

    workflow.add_edge(START, "init_context")
    workflow.add_edge("init_context", "build_dataset")
    workflow.add_edge("build_dataset", "validate_dataset")
    workflow.add_edge("validate_dataset", "evaluate_dataset")
    workflow.add_edge("evaluate_dataset", "write_report")
    workflow.add_edge("write_report", "print_summary")
    workflow.add_edge("print_summary", END)

    compiled = workflow.compile()
    write_graph_image(compiled, graph_path, node_names)
    return compiled


async def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("elasticsearch").setLevel(logging.WARNING)
    args = parse_args()
    graph = create_eval_graph(args.graph_path)
    await graph.ainvoke({"args": args})


if __name__ == "__main__":
    asyncio.run(main())
