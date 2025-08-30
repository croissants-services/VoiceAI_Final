## backend

This is a description of running the backend code in typescript language. It is written under the premise that you have never run typescript on your local device before.

### 1. Node.js + npm 설치

To install Node.js and nps, please type the code below to your command line
```bash
# Homebrew가 없다면 먼저 설치: https://brew.sh/
brew install node

# 확인
node -v
npm -v
```

Otherwise, you can download the official file from https://nodejs.org/.
Download the LTS version, and verify the installation by running

```bash
node -v
npm -v
```

### 2. Initialize project

Move your current working directory to /backend, and initialize the project root. Then you'll get package.json created on your directory

```bash
cd /path/to/backend
npm init -y
```

Have in mind that the backend directory wraps both /src and .json files required for the packages.

### 3. Install Dependencies

```bash
npm install ws

npm install -D typescript ts-node-dev @types/node @types/ws
```

### 4. Confirm the location of the source codes, it should be equivalent to the following.

```
project-root/
  src/
    server.ts
    services/
      ModelApiService.ts
    websocket/
      gateway.ts
  package.json
  tsconfig.json (다음 단계에서 생성)
```

### 5. Create TypeScript Settings file under the /src directory

Under /backend, you should create tsconfig.json file, which should be equivalent to below.

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "CommonJS",
    "moduleResolution": "Node",
    "esModuleInterop": true,
    "strict": true,
    "outDir": "dist",
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

### 6. Add running script to your 'scripts' section on package.json

Please add 'dev' thread to the 'scripts' section on the package.json.

```json
{
  "scripts": {
    "dev": "ts-node-dev --respawn src/server.ts"
  }
}
```

### 7. Set up environment variables

The code below sets the FastAPI model server WS address and BFF port which BFF will connect to as an environemt variable.

```bash
export MODEL_WS_URL=ws://127.0.0.1:8000/ws/s2s
export PORT=3001
```

### 8. Verify if the server is running

```bash
npm run dev
```

By running the code above, you can visualize if the BFF is working properly.
