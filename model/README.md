# STT + LLM + TTS

This is a Speech-to-Speech AI chatbot project. It transcribes the user's voice to text (STT), retrieves information using RAG to generate a response with a Large Language Model (LLM), and then synthesizes the response back into speech (TTS) for the user.

---

## Getting Started

### 1. Install Requirements

First, create a virtual environment and install the necessary Python packages:

```bash
pip install -r requirements.txt
```

---

### 2. Set Up API Key

Create a file named `.env` in the root directory and add your OpenAI API key:

```env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPGRAM_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

In your Python code, load the environment variables using:

```python
from dotenv import load_dotenv
load_dotenv(".env")
```

---

### 3. Make Vectorstore

Using txt files, create FAISS Vectorstore and save to local:

```python
python rag/make_vectorstore.py
```

---

### 4. Run the Project
You will need two terminals to run the project.

#### Terminal 1: Run the Server

First, run the command below to start the FastAPI server. This server handles all the AI processing.

```bash
python -m uvicorn model.main:app --reload
```

#### Terminal 2: Run the Client

While the server is running, open a new terminal and run the command below to start the test client. You can use this client to provide voice input via your microphone and hear the AI's spoken response.
```bash
python temp_test_folder/client_test_speech.py
```
