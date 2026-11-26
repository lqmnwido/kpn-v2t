# Build stage
FROM maven:3.9.6-eclipse-temurin-21 AS builder
WORKDIR /app
COPY pom.xml ./pom.xml
COPY asr-api/pom.xml ./asr-api/pom.xml
COPY temporal-worker/pom.xml ./temporal-worker/pom.xml
COPY asr-api/src ./asr-api/src
RUN mvn dependency:go-offline -q -pl asr-api -am
RUN mvn clean package -DskipTests -q -pl asr-api -am

# Run stage
FROM eclipse-temurin:21-jre-jammy
WORKDIR /app
COPY --from=builder /app/asr-api/target/*.jar app.jar
EXPOSE 8081
ENTRYPOINT ["java", "-jar", "app.jar"]