from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, ArrayType

# Initialize SparkSession
spark = SparkSession.builder.appName("DatalakeRawImport").getOrCreate()

# Read from Parquet files in the 'spark-sinker' directory
df = spark.read.parquet("spark-sinker")

# Show the DataFrame
df.show()