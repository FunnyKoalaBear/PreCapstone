#listens for client connections 
from db import open_connection, close_connection, add_query, print_table 


# main.py
conn, cur = open_connection()
print_table(cur)
add_query(conn, cur)
print_table(cur) 
close_connection(conn, cur)

