#!/usr/bin/env python3
import sqlite3
import json
import sys

db_path = "/Users/tciuser1/qqbot/astrbot/data/data_v4.db"

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 查询所有 preferences
    cursor.execute("SELECT key, value FROM preferences")
    rows = cursor.fetchall()
    
    print("=== Current preferences related to qq_napcat ===")
    for key, value in rows:
        if 'qq_napcat' in key or 'napcat' in key or 'qq' in key:
            print(f"{key}: {value}")
    
    # 查询 platform_sessions 看看 NapCat 的连接配置
    cursor.execute("SELECT * FROM platform_sessions LIMIT 5")
    sessions = cursor.fetchall()
    print(f"\n=== platform_sessions columns ===")
    if sessions:
        for row in sessions:
            print(row)
    
    conn.close()
    
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
