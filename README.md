Proyecto FInal 28/05/26

Usar IAC

# FASE A
ETL
Ingesta de datos (S3)
Glue crawler -> Glue data catalog
Glue job (Py Spark) -> (S3 guardarlos en formato parquet)

# Fase B 
1.- Entrenar: SageMaker
2.- Despliegue: SageMaker endpoint
3.- Inferencia(lambda)-> Desplegar un sevicio de inferencia, un demonio ejecutandose en 2do plano. onnx

# Fase C
1.-Dashboard BI
2.- Widget1 - Glue job
    Widget2 - Contador archivos S3
    Widget3 - Tasa de error de endpoint SageMaker, dependiendo del dataset tenemos que elegir metricas
                Dado que se puede tener un datadrift, y en este caso se tiene que reentrenar y actualizar
                endpoint
    SNS -> Notificacion al ing de machine learning que cheque el modelo.
    
# Fase D
CI/CD



