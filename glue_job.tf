resource "aws_glue_job" "data_transformation_job" {
  name     = "garbage-data-etl"
  role_arn = aws_iam_role.glue_role.arn
  glue_version = "5.0"
  command {
    script_location = "s3://${aws_s3_bucket.code.bucket}/scripts/data_transformation.py"
    python_version = "3"
  }
  default_arguments = {
    "--additional-python-modules" = "Pillow" # Glue will install this for you!
    "--DATABASE_NAME" = aws_glue_catalog_database.garbage_db.name
    "--job-language"  = "python"
  }
}


resource "aws_glue_trigger" "start_job_trigger" {
  name          = "crawler-ends-starts-job"
  type          = "CONDITIONAL" 
  workflow_name = aws_glue_workflow.garbage_wf.name

  predicate {
    conditions {
      crawler_name = aws_glue_crawler.garbage_crawler.name
      # CRITICAL: It must be crawl_state, not just state
      crawl_state  = "SUCCEEDED" 
      logical_operator = "EQUALS"
    }
  }

  actions {
    job_name = aws_glue_job.data_transformation_job.name
  }
}
