import os
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, ArrayType

# 1️⃣ Initialize SparkSession with Kafka support
spark = (
    SparkSession.builder
    .appName("KafkaStream")
    .config(
        "spark.jars.packages",
        "org.apache.spark:spark-sql-kafka-0-10_2.12:3.0.3"
    )
    .master("local[*]")
    .getOrCreate()
)

spark.sparkContext.setLogLevel("WARN")

# 2️⃣ Read from Kafka topic
# Note: Adjust localhost:9092 if your container advertises differently
df = (
    spark.readStream
    .format("kafka")
    .option("kafka.bootstrap.servers", "localhost:9092")
    .option("subscribe", "customer-profile")
    .option("startingOffsets", "latest")  # or "earliest", "latest"
    .load()
)

# 3️⃣ Kafka schema: key and value are binary (bytes)
# Convert value to string and parse JSON
parsed_df = df.selectExpr("CAST(value AS STRING) as json_string")

# 4️⃣ Parse JSON to extract customer profile fields
schema = StructType([
    StructField("email", StringType(), True),
    StructField("diet_type", StringType(), True),
    StructField("allergies", ArrayType(StringType()), True),
    StructField("budget", DoubleType(), True),
    StructField("cuisine", StringType(), True),
    StructField("loaded_at", StringType(), True),
    StructField("processed_at", StringType(), True)
])

customer_profile_df = parsed_df.select(
    from_json(col("json_string"), schema).alias("data")
).select("data.*")

# 5️⃣ Write streaming output to parquet files
output_path = os.path.join(os.getcwd(), "spark-sinker")

query = (
    customer_profile_df.writeStream
    .trigger(processingTime="5 seconds")
    .outputMode("append")  # append mode for parquet files
    .format("parquet")
    .option("path", output_path)
    .option("checkpointLocation", f"{output_path}/checkpoint")  # Required for streaming
    .start()
)

print(f"Streaming started. Writing parquet files to: {output_path}")
print("Waiting for messages from Kafka topic 'customer-profile'...")

query.awaitTermination()
