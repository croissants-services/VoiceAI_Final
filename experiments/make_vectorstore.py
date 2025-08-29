import os, glob, re
from typing import List
from langchain_community.embeddings import HuggingFaceEmbeddings
#from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


# ====== 0) 설정 ======
INPUT_DIR = "docs"            # .txt 파일 폴더 경로
INDEX_DIR = "faiss_kb"          # 저장 폴더
CHUNK_SIZE = 500                # 한국어 권장 시작값(350~500)
CHUNK_OVERLAP = 100              # 50~100 사이 권장
MIN_SECTION_LEN_TO_SPLIT = 1200 # 너무 긴 섹션만 토큰 스플릿
MODEL_NAME = "jhgan/ko-sroberta-multitask"


# ====== 1) 제목 인젝션 유틸(질문자가 준 패턴) ======
def make_doc(chunk_text: str, breadcrumbs: List[str], path: str) -> Document:
    title = " > ".join(breadcrumbs) if breadcrumbs else "Untitled"
    prefixed = f"[섹션] {title}\n{chunk_text.strip()}"
    return Document(
        page_content=prefixed,
        metadata={"source": path, "title": title, "breadcrumbs": breadcrumbs},
    )


# ====== 2) '# 제목'만 인지해서 1차 분할 ======
# - 라인 시작에 '#' 1개만(## 이상은 무시) 허용
# - '#제목'처럼 공백이 없어도 허용 → '# 제목'으로 정규화
HDR = re.compile(r"(?m)^\s*#(?!#)\s*(.+?)\s*$")  # ^# (not ##)

def split_by_h1(text: str):
    # '#제목' → '# 제목' 정규화
    text = re.sub(r"(?m)^\s*#(?!#)(\S)", r"# \1", text)
    matches = list(HDR.finditer(text))
    if not matches:
        return [{"title": "Untitled", "body": text.strip()}]

    out = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            out.append({"title": title, "body": body})
    return out


# ====== 3) 파일 단위 처리: 1차(H1) → 길면 토큰 스플릿 → Document化 ======
rec_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
    chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
)


def process_file(path: str) -> List[Document]:
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    sections = split_by_h1(raw)
    docs: List[Document] = []

    for sec in sections:
        title = sec["title"]
        body = sec["body"]

        # 길면 토큰 스플릿, 아니면 통째로
        if len(body) > MIN_SECTION_LEN_TO_SPLIT:
            chunks = rec_splitter.split_text(body)
        else:
            chunks = [body]

        for c in chunks:
            docs.append(make_doc(c, breadcrumbs=[title], path=path))
    return docs


# ====== 4) 전체 폴더 처리 → FAISS 색인 생성/저장 ======
def build_and_save_index():
    files = glob.glob(os.path.join(INPUT_DIR, "**/*.txt"), recursive=True)
    if not files:
        raise FileNotFoundError(f"No .txt files found under: {INPUT_DIR}")

    all_docs: List[Document] = []
    for fp in files:
        all_docs.extend(process_file(fp))

    # 임베딩(ko/en 혼용 권장 모델)
    embedding = HuggingFaceEmbeddings(
        model_name=MODEL_NAME,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    vs = FAISS.from_documents(all_docs, embedding)
    os.makedirs(INDEX_DIR, exist_ok=True)
    vs.save_local(INDEX_DIR)
    print(f"[OK] Saved FAISS index to: {INDEX_DIR} (docs={len(all_docs)})")


# ====== 5) 로드 & 테스트 검색 ======
def quick_search(query: str, k: int = 5):
    embedding = HuggingFaceEmbeddings(
        model_name=MODEL_NAME,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    vs = FAISS.load_local(INDEX_DIR, embedding, allow_dangerous_deserialization=True)
    hits = vs.similarity_search(query, k=k)
    for i, h in enumerate(hits, 1):
        print(f"\n[{i}] {h.metadata.get('title')}  ({h.metadata.get('source')})")
        print(h.page_content.replace("\n", " "))


if __name__ == "__main__":
    # 1) 인덱스 생성/저장
    build_and_save_index()

    # 2) 간단 검색 확인
    # 예: quick_search("주휴수당 지급 시점은?")
    # quick_search("시급제에서 정규직 전환시 연차 계산", k=3)
