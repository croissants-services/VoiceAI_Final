# Voice AI Assistant

This project is a full-stack voice-based AI assistant. It consists of a React frontend, a Node.js backend (BFF), and a Python backend for AI model processing (STT, LLM, TTS).

## Prerequisites

Before you begin, ensure you have the following installed on your system:

-   **Node.js**: Version 20.19+ or 22.12+. You can download it from [nodejs.org](https://nodejs.org/).
-   **pnpm**: After installing Node.js, you can install pnpm globally by running:
    ```bash
    npm install -g pnpm
    ```
-   **Python**: Version 3.10 or higher. You can download it from [python.org](https://www.python.org/). Make sure to add Python to your system's PATH during installation.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/jiucai233/VoiceAI_Final.git
    cd VoiceAI_Final
    ```

2.  **Install Node.js dependencies:**
    This command will install dependencies for both the `frontend` and `backend` workspaces.
    ```bash
    pnpm install
    ```

## Running the Application

To run the application, you need to start two separate processes in two different terminals.

### Terminal 1: Start Frontend and Backend Servers

In your first terminal, at the project root, run the following command. This will start the React frontend development server (on port 5173) and the Node.js backend server (on port 3001).

```bash
pnpm run dev
```

### Terminal 2: Start the Python AI Model Server

The AI model server requires a Python virtual environment. Open a second terminal at the project root to run these commands.

1.  **Create and activate the virtual environment:**

    -   **For Windows (Command Prompt / PowerShell):**
        ```cmd
        python -m venv .venv
        .venv\Scripts\activate
        ```

    -   **For macOS / Linux (bash / zsh):**
        ```bash
        python3 -m venv .venv
        source .venv/bin/activate
        ```

2.  **Install Python dependencies:**
    Once the virtual environment is activated, install the required packages.
    ```bash
    pip install -r model/requirements.txt
    ```

3.  **Run the AI Model Server:**
    Finally, start the FastAPI server using uvicorn. It will run on port 8000.
    ```bash
    uvicorn model.main:app --host 127.0.0.1 --port 8000
    ```

## Accessing the Application

Once all services are running, you can access the Voice AI Assistant by opening your web browser and navigating to:

[http://localhost:5173](http://localhost:5173)

The application should now be fully functional.
