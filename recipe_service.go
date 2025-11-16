package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/IBM/sarama"
	"github.com/redis/go-redis/v9"
)

// Recipe represents a recipe structure
type Recipe struct {
	Name        string  `json:"name"`
	Cuisine     string  `json:"cuisine"`
	Description string  `json:"description"`
	Ingredients string  `json:"ingredients"`
	Price       float64 `json:"price"`
	TimeToCook  int     `json:"time_to_cook"`
	DietType    string  `json:"diet_type"`
	ImageURL    string  `json:"image_url"`
}

// RecipeResponse represents the API response structure
type RecipeResponse struct {
	Recipes []Recipe `json:"recipes"`
	Count   int      `json:"count"`
}

// Preferences represents user preferences
type Preferences struct {
	LoadedAt    string    `json:"loaded_at,omitempty"`
	ProcessedAt time.Time `json:"processed_at,omitempty"`
	Email       string    `json:"email"`
	DietType    string    `json:"diet_type"`
	Allergies   []string  `json:"allergies"`
	Budget      float64   `json:"budget"`
	Cuisine     string    `json:"cuisine"`
}

var kafkaProducer sarama.SyncProducer
var redisClient *redis.Client

// loadRecipesFromCSV loads recipes from the CSV file
func loadRecipesFromCSV(filename string) error {
	file, err := os.Open(filename)
	if err != nil {
		return fmt.Errorf("error opening CSV file: %v", err)
	}
	defer file.Close()

	reader := csv.NewReader(file)
	records, err := reader.ReadAll()
	if err != nil {
		return fmt.Errorf("error reading CSV: %v", err)
	}

	// Skip header row
	for i := 1; i < len(records); i++ {
		record := records[i]
		if len(record) < 8 {
			continue
		}

		price, err := strconv.ParseFloat(record[4], 64)
		if err != nil {
			log.Printf("Error parsing price for recipe %s: %v", record[0], err)
			continue
		}

		timeToCook, err := strconv.Atoi(record[5])
		if err != nil {
			log.Printf("Error parsing time_to_cook for recipe %s: %v", record[0], err)
			continue
		}

		recipe := Recipe{
			Name:        record[0],
			Cuisine:     record[1],
			Description: record[2],
			Ingredients: record[3],
			Price:       price,
			TimeToCook:  timeToCook,
			DietType:    record[6],
			ImageURL:    record[7],
		}

		_ = recipe // Recipe parsed but not stored (function kept for backward compatibility)
	}

	return nil
}

// loadRecipesToRedis loads recipes from CSV and stores them in Redis
func loadRecipesToRedis(filename string) error {
	if redisClient == nil {
		return fmt.Errorf("redis client not initialized")
	}

	// Load recipes from CSV
	var recipesList []Recipe
	file, err := os.Open(filename)
	if err != nil {
		return fmt.Errorf("error opening CSV file: %v", err)
	}
	defer file.Close()

	reader := csv.NewReader(file)
	records, err := reader.ReadAll()
	if err != nil {
		return fmt.Errorf("error reading CSV: %v", err)
	}

	// Skip header row
	for i := 1; i < len(records); i++ {
		record := records[i]
		if len(record) < 8 {
			continue
		}

		price, err := strconv.ParseFloat(record[4], 64)
		if err != nil {
			log.Printf("Error parsing price for recipe %s: %v", record[0], err)
			continue
		}

		timeToCook, err := strconv.Atoi(record[5])
		if err != nil {
			log.Printf("Error parsing time_to_cook for recipe %s: %v", record[0], err)
			continue
		}

		recipe := Recipe{
			Name:        record[0],
			Cuisine:     record[1],
			Description: record[2],
			Ingredients: record[3],
			Price:       price,
			TimeToCook:  timeToCook,
			DietType:    record[6],
			ImageURL:    record[7],
		}

		recipesList = append(recipesList, recipe)
	}

	// Convert recipes to JSON
	recipesJSON, err := json.Marshal(recipesList)
	if err != nil {
		return fmt.Errorf("failed to marshal recipes: %v", err)
	}

	// Store in Redis
	ctx := context.Background()
	key := "recipes:all"
	if err := redisClient.Set(ctx, key, recipesJSON, 0).Err(); err != nil {
		return fmt.Errorf("failed to store recipes in Redis: %v", err)
	}

	log.Printf("Loaded %d recipes from CSV to Redis", len(recipesList))
	return nil
}

// getRecipesFromRedis retrieves all recipes from Redis
func getRecipesFromRedis() ([]Recipe, error) {
	if redisClient == nil {
		return nil, fmt.Errorf("redis client not initialized")
	}

	ctx := context.Background()
	key := "recipes:all"
	recipesJSON, err := redisClient.Get(ctx, key).Result()
	if err == redis.Nil {
		return nil, fmt.Errorf("no recipes found in Redis")
	} else if err != nil {
		return nil, fmt.Errorf("failed to get recipes from Redis: %v", err)
	}

	var recipesList []Recipe
	if err := json.Unmarshal([]byte(recipesJSON), &recipesList); err != nil {
		return nil, fmt.Errorf("failed to unmarshal recipes: %v", err)
	}

	return recipesList, nil
}

// filterRecipesFromList filters a list of recipes based on query parameters
func filterRecipesFromList(recipesList []Recipe, dietType, cuisine string, allergies []string, budget float64) []Recipe {
	var filtered []Recipe

	for _, recipe := range recipesList {
		// Filter by diet type (case-insensitive)
		if dietType != "" && !strings.EqualFold(recipe.DietType, dietType) {
			continue
		}

		// Filter by cuisine (case-insensitive)
		if cuisine != "" && !strings.EqualFold(recipe.Cuisine, cuisine) {
			continue
		}

		// Filter by budget
		if budget > 0 && recipe.Price > budget {
			continue
		}

		// Filter by allergies (exclude recipes containing allergenic ingredients)
		if len(allergies) > 0 {
			shouldExclude := false
			ingredientsLower := strings.ToLower(recipe.Ingredients)

			for _, allergy := range allergies {
				allergyLower := strings.ToLower(allergy)
				if strings.Contains(ingredientsLower, allergyLower) {
					shouldExclude = true
					break
				}
			}

			if shouldExclude {
				continue
			}
		}

		filtered = append(filtered, recipe)
	}

	return filtered
}

// recipesHandler handles GET /api/recipes
func recipesHandler(w http.ResponseWriter, r *http.Request) {
	// Set CORS headers
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	// Handle OPTIONS request for CORS
	if r.Method == "OPTIONS" {
		w.WriteHeader(http.StatusOK)
		return
	}

	// Only allow GET method
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get recipes from Redis
	recipesList, err := getRecipesFromRedis()
	if err != nil {
		log.Printf("Error getting recipes from Redis: %v", err)
		http.Error(w, fmt.Sprintf("Failed to retrieve recipes: %v", err), http.StatusInternalServerError)
		return
	}

	// Check if email is provided - if so, fetch preferences from Redis
	email := r.URL.Query().Get("email")
	var dietType, cuisine string
	var allergies []string
	var budget float64

	if email != "" {
		// Fetch preferences from Redis
		preferences, err := getPreferencesFromRedis(email)
		if err == nil && preferences != nil {
			// Use preferences from Redis
			dietType = preferences.DietType
			cuisine = preferences.Cuisine
			allergies = preferences.Allergies
			budget = preferences.Budget
			log.Printf("Using preferences from Redis for email: %s", email)
		} else {
			log.Printf("No preferences found in Redis for email: %s, returning all recipes", email)
			// If no preferences found, return all recipes (no filtering)
			dietType = ""
			cuisine = ""
			allergies = nil
			budget = 0
		}
	} else {
		// No email provided, return all recipes (no filtering)
		log.Println("No email provided, returning all recipes")
		dietType = ""
		cuisine = ""
		allergies = nil
		budget = 0
	}

	// Filter recipes based on preferences (or return all if no preferences)
	filteredRecipes := filterRecipesFromList(recipesList, dietType, cuisine, allergies, budget)

	// Print filtered recipes
	log.Println("=== Filtered Recipes ===")
	log.Printf("Total recipes in database: %d", len(recipesList))
	log.Printf("Filtered recipes count: %d", len(filteredRecipes))
	if email != "" {
		log.Printf("Filtering for email: %s", email)
		log.Printf("Applied filters - Diet Type: %s, Cuisine: %s, Budget: %.2f, Allergies: %v", dietType, cuisine, budget, allergies)
	} else {
		log.Println("No email provided - returning all recipes (no filters applied)")
	}
	log.Println("Filtered recipe names:")
	for i, recipe := range filteredRecipes {
		log.Printf("  %d. %s (Cuisine: %s, Diet: %s, Price: %.2f, Time: %d mins)",
			i+1, recipe.Name, recipe.Cuisine, recipe.DietType, recipe.Price, recipe.TimeToCook)
	}
	log.Println("========================")

	// Create response
	response := RecipeResponse{
		Recipes: filteredRecipes,
		Count:   len(filteredRecipes),
	}

	// Encode and send response
	if err := json.NewEncoder(w).Encode(response); err != nil {
		http.Error(w, "Error encoding response", http.StatusInternalServerError)
		return
	}
}

// loadRecipesHandler handles POST /api/recipes/load
func loadRecipesHandler(w http.ResponseWriter, r *http.Request) {
	// Set CORS headers
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	// Handle OPTIONS request for CORS
	if r.Method == "OPTIONS" {
		w.WriteHeader(http.StatusOK)
		return
	}

	// Only allow POST method
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get CSV filename from query parameter or use default
	csvFile := r.URL.Query().Get("file")
	if csvFile == "" {
		csvFile = "recipe_database.csv"
	}

	// Load recipes from CSV to Redis
	if err := loadRecipesToRedis(csvFile); err != nil {
		log.Printf("Error loading recipes to Redis: %v", err)
		http.Error(w, fmt.Sprintf("Failed to load recipes: %v", err), http.StatusInternalServerError)
		return
	}

	// Send success response
	response := map[string]interface{}{
		"status":  "success",
		"message": fmt.Sprintf("Recipes loaded from %s to Redis successfully", csvFile),
	}

	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(response); err != nil {
		log.Printf("Error encoding response: %v", err)
	}
}

// preferencesHandler handles POST /api/preferences
func preferencesHandler(w http.ResponseWriter, r *http.Request) {
	// Set CORS headers
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	// Handle OPTIONS request for CORS
	if r.Method == "OPTIONS" {
		w.WriteHeader(http.StatusOK)
		return
	}

	// Only allow POST method
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Decode JSON body
	var preferences Preferences
	if err := json.NewDecoder(r.Body).Decode(&preferences); err != nil {
		log.Printf("Error decoding JSON: %v", err)
		http.Error(w, fmt.Sprintf("Invalid JSON body: %v", err), http.StatusBadRequest)
		return
	}

	// Validate required fields
	if preferences.Email == "" {
		http.Error(w, "Email is required", http.StatusBadRequest)
		return
	}

	// Print preferences to console
	log.Println("=== Received Preferences ===")
	if preferences.LoadedAt != "" {
		log.Printf("Loaded At: %s", preferences.LoadedAt)
	}
	log.Printf("Email: %s", preferences.Email)
	log.Printf("Diet Type: %s", preferences.DietType)
	log.Printf("Cuisine: %s", preferences.Cuisine)
	log.Printf("Budget: %.2f", preferences.Budget)
	log.Printf("Allergies: %v", preferences.Allergies)
	log.Println("===========================")
	preferences.ProcessedAt = time.Now()
	// Update preferences in Redis (check if exists, then add or update)
	if err := updateRedisPreferences(preferences); err != nil {
		log.Printf("Error updating Redis: %v", err)
		// Continue processing even if Redis fails
	}

	// Send message to Kafka
	if err := sendToKafka(preferences); err != nil {
		log.Printf("Error sending to Kafka: %v", err)
		// Continue processing even if Kafka fails
	}

	// Send success response
	response := map[string]interface{}{
		"status":      "success",
		"message":     "Preferences received",
		"preferences": preferences,
	}

	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(response); err != nil {
		log.Printf("Error encoding response: %v", err)
	}
}

// initRedisClient initializes the Redis client
func initRedisClient() error {
	redisAddr := os.Getenv("REDIS_ADDR")
	if redisAddr == "" {
		redisAddr = "localhost:6379" // Default for Docker
	}

	redisPassword := os.Getenv("REDIS_PASSWORD")
	if redisPassword == "" {
		redisPassword = "" // No password by default
	}

	redisClient = redis.NewClient(&redis.Options{
		Addr:     redisAddr,
		Password: redisPassword,
		DB:       0, // Default DB
	})

	// Test connection
	ctx := context.Background()
	_, err := redisClient.Ping(ctx).Result()
	if err != nil {
		return fmt.Errorf("failed to connect to Redis: %v", err)
	}

	log.Printf("Redis client initialized successfully (addr: %s)", redisAddr)
	return nil
}

// updateRedisPreferences checks if preferences exist for email, then adds or updates
func updateRedisPreferences(preferences Preferences) error {
	if redisClient == nil {
		return fmt.Errorf("redis client not initialized")
	}

	ctx := context.Background()
	email := preferences.Email
	key := fmt.Sprintf("customer:preferences:%s", email)

	// Convert preferences to JSON
	preferencesJSON, err := json.Marshal(preferences)
	if err != nil {
		return fmt.Errorf("failed to marshal preferences: %v", err)
	}

	// Check if key exists for logging purposes
	exists, err := redisClient.Exists(ctx, key).Result()
	if err != nil {
		return fmt.Errorf("failed to check Redis key: %v", err)
	}

	// Set the key (Redis SET creates if not exists, updates if exists)
	if err := redisClient.Set(ctx, key, preferencesJSON, 0).Err(); err != nil {
		return fmt.Errorf("failed to set Redis key: %v", err)
	}

	// Log whether it was added or updated
	if exists == 0 {
		log.Printf("Added new preferences to Redis for email: %s", email)
	} else {
		log.Printf("Updated existing preferences in Redis for email: %s", email)
	}

	return nil
}

// getPreferencesFromRedis retrieves preferences for a given email
func getPreferencesFromRedis(email string) (*Preferences, error) {
	if redisClient == nil {
		return nil, fmt.Errorf("redis client not initialized")
	}

	ctx := context.Background()
	key := fmt.Sprintf("customer:preferences:%s", email)

	preferencesJSON, err := redisClient.Get(ctx, key).Result()
	if err == redis.Nil {
		return nil, fmt.Errorf("no preferences found for email: %s", email)
	} else if err != nil {
		return nil, fmt.Errorf("failed to get preferences from Redis: %v", err)
	}

	var preferences Preferences
	if err := json.Unmarshal([]byte(preferencesJSON), &preferences); err != nil {
		return nil, fmt.Errorf("failed to unmarshal preferences: %v", err)
	}

	return &preferences, nil
}

// initKafkaProducer initializes the Kafka producer
func initKafkaProducer() error {
	kafkaBrokers := os.Getenv("KAFKA_BROKERS")
	if kafkaBrokers == "" {
		kafkaBrokers = "localhost:9092" // Default for Docker
	}

	config := sarama.NewConfig()
	config.Producer.Return.Successes = true
	config.Producer.RequiredAcks = sarama.WaitForAll
	config.Producer.Retry.Max = 5
	config.Producer.Timeout = 10 * time.Second

	brokers := []string{kafkaBrokers}
	producer, err := sarama.NewSyncProducer(brokers, config)
	if err != nil {
		return fmt.Errorf("failed to create Kafka producer: %v", err)
	}

	kafkaProducer = producer
	log.Printf("Kafka producer initialized successfully (brokers: %s)", kafkaBrokers)
	return nil
}

// sendToKafka sends preferences to Kafka topic
func sendToKafka(preferences Preferences) error {
	if kafkaProducer == nil {
		return fmt.Errorf("kafka producer not initialized")
	}

	// Convert preferences to JSON
	messageJSON, err := json.Marshal(preferences)
	if err != nil {
		return fmt.Errorf("failed to marshal preferences: %v", err)
	}

	// Create Kafka message
	topic := "customer-profile"
	message := &sarama.ProducerMessage{
		Topic: topic,
		Value: sarama.StringEncoder(messageJSON),
		Key:   sarama.StringEncoder(preferences.Email), // Use email as key for partitioning
	}

	// Send message
	partition, offset, err := kafkaProducer.SendMessage(message)
	if err != nil {
		return fmt.Errorf("failed to send message to Kafka: %v", err)
	}

	log.Printf("Message sent to Kafka topic '%s' (partition: %d, offset: %d)", topic, partition, offset)
	return nil
}

// healthHandler handles GET /health for health checks
func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "healthy"})
}

func main() {
	// Note: Recipes are now loaded via POST /api/recipes/load endpoint
	// The old loadRecipesFromCSV is kept for backward compatibility but not called on startup

	// Initialize Redis client
	if err := initRedisClient(); err != nil {
		log.Printf("Warning: Failed to initialize Redis client: %v", err)
		log.Println("Service will continue without Redis support")
	} else {
		// Ensure Redis client is closed on exit
		defer func() {
			if redisClient != nil {
				if err := redisClient.Close(); err != nil {
					log.Printf("Error closing Redis client: %v", err)
				} else {
					log.Println("Redis client closed successfully")
				}
			}
		}()
	}

	// Initialize Kafka producer
	if err := initKafkaProducer(); err != nil {
		log.Printf("Warning: Failed to initialize Kafka producer: %v", err)
		log.Println("Service will continue without Kafka support")
	} else {
		// Ensure producer is closed on exit
		defer func() {
			if kafkaProducer != nil {
				if err := kafkaProducer.Close(); err != nil {
					log.Printf("Error closing Kafka producer: %v", err)
				} else {
					log.Println("Kafka producer closed successfully")
				}
			}
		}()
	}

	// Setup routes
	http.HandleFunc("/api/recipes", recipesHandler)
	http.HandleFunc("/api/recipes/load", loadRecipesHandler)
	http.HandleFunc("/api/preferences", preferencesHandler)
	http.HandleFunc("/health", healthHandler)

	// Start server
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	log.Printf("Starting server on port %s", port)
	log.Printf("API endpoints:")
	log.Printf("  GET  http://localhost:%s/api/recipes", port)
	log.Printf("  POST http://localhost:%s/api/recipes/load", port)
	log.Printf("  POST http://localhost:%s/api/preferences", port)
	log.Printf("  GET  http://localhost:%s/health", port)

	if err := http.ListenAndServe(":"+port, nil); err != nil {
		log.Fatalf("Server failed to start: %v", err)
	}
}
