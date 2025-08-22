from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
def home():
    return {"message":"hello surya"}
