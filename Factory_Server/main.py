#listens for client connections 
from fastapi import FastAPI
from pydantic import BaseModel

from db import open_connection, close_connection, add_query, print_table 


#pydantic scan class 
class ScanData(BaseModel):
    time: int
    order: float
    step: float


class TestData(BaseModel):
    uid: str

#open connections
app = FastAPI() 


#network paths 
@app.post("/record_scan")
def recieve_data(data: TestData):

    #connecting to db
    conn, cur = open_connection()

    print(f"Received uid {data.uid}")

    #sending query
    try: 
        add_query(conn, cur, data)
        print_table(cur) 
    finally:
        close_connection(conn, cur)

    return {"ok": True}



    

# main.py
# conn, cur = open_connection()
# print_table(cur)
# add_query(conn, cur)
# print_table(cur) 
# close_connection(conn, cur)

