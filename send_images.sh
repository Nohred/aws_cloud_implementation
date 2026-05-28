#!/bin/bash

# Configuration
SOURCE_DIR="garbage_classification"
S3_BUCKET="s3://waste-classifier-dev-inference-v3"
NUM_IMAGES=2

# Check if the dataset directory exists
if [ ! -d "$SOURCE_DIR" ]; then
  echo "Error: Directory '$SOURCE_DIR' not found in the current path."
  exit 1
fi

echo "Selecting $NUM_IMAGES random images from '$SOURCE_DIR'..."
echo "Target Bucket: $S3_BUCKET"
echo "============================================================"

# Find all images, shuffle them randomly, and grab the first 10
find "$SOURCE_DIR" -type f \( -iname \*.jpg -o -iname \*.jpeg -o -iname \*.png \) | sort -R | head -n "$NUM_IMAGES" | while read -r filepath; do
    
    # Extract the true class (parent folder name) and the file name
    class_name=$(basename "$(dirname "$filepath")")
    file_name=$(basename "$filepath")
    
    # Create a unique destination key so we know the true class from the SNS email
    dest_key="${class_name}_${file_name}"
    
    echo "Expected Class : [$class_name]"
    echo "Local File     : $filepath"
    echo "Uploading to   : $S3_BUCKET/$dest_key"
    
    # Execute the AWS CLI copy command
    aws s3 cp "$filepath" "$S3_BUCKET/$dest_key" --quiet
    
    echo "------------------------------------------------------------"
done

echo "Done! Check your email for SNS notifications to see the predictions."