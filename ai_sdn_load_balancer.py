import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
import random
import time

# Simulated network paths
paths = {
    "Path A": 0,
    "Path B": 0,
    "Path C": 0
}

# Generate training traffic data
data = {
    "traffic": [10,20,30,40,50,60,70,80,90,100],
    "latency": [5,8,10,15,20,25,30,35,40,45]
}

df = pd.DataFrame(data)

X = df[["traffic"]]
y = df["latency"]

# Train ML model
model = LinearRegression()
model.fit(X, y)

print("AI/ML Load Balancer Started...\n")

while True:

    # Simulate incoming traffic
    incoming_traffic = random.randint(10,100)

    # Predict latency
    predicted_latency = model.predict([[incoming_traffic]])

    print("Incoming Traffic:", incoming_traffic)
    print("Predicted Latency:", round(predicted_latency[0],2))

    # Choose path with minimum load
    best_path = min(paths, key=paths.get)

    print("Routing traffic through:", best_path)

    # Update path load
    paths[best_path] += incoming_traffic

    print("Current Network Load:", paths)
    print("--------------------------------")

    time.sleep(3)