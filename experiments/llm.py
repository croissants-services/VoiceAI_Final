import asyncio
import os
from dotenv import load_dotenv
import openai

# Langchain 및 LangGraph 관련 라이브러리 임포트
from langchain_community.vectorstores import FAISS
from langchain.embeddings import HuggingFaceEmbeddings
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

# .env 파일에서 환경 변수를 로드합니다.
load_dotenv(dotenv_path=".env")

class LLMModel:
    def __init__(self):
        """
        LLM 모델과 RAG에 필요한 구성 요소를 초기화합니다.
        서버 시작 시 한 번만 실행됩니다.
        """
        print("LLMModel 초기화 시작...")
        # 1. LLM 초기화 (비동기 클라이언트 사용)
        self.llm = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # 2. Embedding 모델 초기화
        print("Embedding 모델 로딩 중...")
        MODEL_NAME = "jhgan/ko-sroberta-multitask"
        self.embeddings = HuggingFaceEmbeddings(
            model_name=MODEL_NAME,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        print("Embedding 모델 로딩 완료.")

        # 3. Vector Store 로드
        print("Vector Store 로딩 중...")
        self.vector_store = FAISS.load_local(
            "./faiss_kb",
            self.embeddings,
            allow_dangerous_deserialization=True
        )
        print("Vector Store 로딩 완료.")
        print("LLMModel 초기화 완료.")

    def retrieve_documents(self, query: str) -> str:
        """
        주어진 쿼리에 대해 RAG를 수행하여 관련 문서를 검색하고 텍스트로 반환합니다.
        (기존 @tool 함수의 로직을 클래스 메서드로 변환)
        """
        print(f"RAG 검색 수행: {query}")
        retrieved_docs = self.vector_store.similarity_search(query, k=2)
        serialized = "\n\n".join(
            (f"Source: {doc.metadata}\nContent: {doc.page_content}")
            for doc in retrieved_docs
        )
        return serialized

    async def generate_text_stream(self, user_prompt: str):
        """
        사용자 프롬프트를 받아 RAG를 수행하고, LLM의 답변을 문장 단위로 스트리밍합니다.
        이 함수는 '비동기 제너레이터'입니다.
        """
        print(f"LLM 입력: {user_prompt}")

        # 1. RAG 수행: LangGraph의 retrieve 로직을 직접 호출합니다.
        retrieved_context = self.retrieve_documents(user_prompt)

        # 2. 시스템 프롬프트 구성
        system_prompt = (
            "You are an assistant for question-answering tasks. "
            "Use the following pieces of retrieved context to answer "
            "the question. If you don't know the answer, say that you "
            "don't know. Use three sentences maximum and keep the "
            "answer concise."
            "\n\n--- Retrieved Context ---\n"
            f"{retrieved_context}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # 3. LLM 스트리밍 API 호출
        stream = await self.llm.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            stream=True,
            temperature=0.1,
            max_tokens=128
        )

        buffer = ""
        # 한국어 문장 종결 부호 추가
        #sentence_delimiters = {".", "?", "!", "습니다", "요"}
        sentence_delimiters = {".", "?", "!"}

        # 4. 토큰을 받아 문장 단위로 조립하고 yield
        async for chunk in stream:
            token = chunk.choices[0].delta.content or ""
            buffer += token

            # --- 오류 수정 부분 ---
            # buffer가 비어있지 않을 때만 문장 분리 로직을 실행합니다.
            if buffer:
                # '습니다.' 처럼 종결 어미와 부호가 같이 오는 경우를 고려
                last_char = buffer[-1]
                last_two_chars = buffer[-2:] if len(buffer) >= 2 else ""

                if last_char in sentence_delimiters or last_two_chars in sentence_delimiters:
                    sentence = buffer.strip()
                    if sentence:
                        print(f"LLM 문장 생성: {sentence}")
                        yield sentence
                        buffer = "" # 버퍼 초기화
        
        # 스트림이 끝난 후 버퍼에 남은 내용이 있으면 마지막 문장으로 처리
        if buffer.strip():
            print(f"LLM 마지막 문장 생성: {buffer.strip()}")
            yield buffer.strip()