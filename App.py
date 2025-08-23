from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
def home():
    hello = input("enter something")
    return hello
    
