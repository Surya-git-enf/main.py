from fastapi import FastAPI, Request
import os
app = FastAPI()

@app.get("/")
def home():
    
    hello = os.getenv("hello")
    return hello
    
