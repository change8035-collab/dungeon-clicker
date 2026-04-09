package com.dungeon;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.*;

@Component
public class SupabaseClient {

    private final HttpClient http = HttpClient.newHttpClient();
    private final ObjectMapper mapper = new ObjectMapper();

    @Value("${supabase.url}")
    private String supabaseUrl;

    @Value("${supabase.key}")
    private String supabaseKey;

    private String restUrl() { return supabaseUrl + "/rest/v1"; }

    private HttpRequest.Builder base(String path) {
        return HttpRequest.newBuilder()
                .uri(URI.create(restUrl() + path))
                .header("apikey", supabaseKey)
                .header("Authorization", "Bearer " + supabaseKey)
                .header("Content-Type", "application/json")
                .header("Prefer", "return=representation");
    }

    public List<Map<String, Object>> select(String table, String columns, String filter) {
        try {
            String path = "/" + table + "?select=" + columns + (filter != null ? "&" + filter : "");
            var req = base(path).GET().build();
            var res = http.send(req, HttpResponse.BodyHandlers.ofString());
            return mapper.readValue(res.body(), new TypeReference<>() {});
        } catch (Exception e) {
            System.err.println("[ERROR] Supabase select " + table + ": " + e.getMessage());
            return List.of();
        }
    }

    public List<Map<String, Object>> selectOrdered(String table, String columns, String orderCol, boolean desc, int limit) {
        try {
            String path = "/" + table + "?select=" + columns + "&order=" + orderCol + (desc ? ".desc" : ".asc") + "&limit=" + limit;
            var req = base(path).GET().build();
            var res = http.send(req, HttpResponse.BodyHandlers.ofString());
            return mapper.readValue(res.body(), new TypeReference<>() {});
        } catch (Exception e) {
            System.err.println("[ERROR] Supabase selectOrdered " + table + ": " + e.getMessage());
            return List.of();
        }
    }

    public void insert(String table, Map<String, Object> data) {
        try {
            var req = base("/" + table)
                    .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(data)))
                    .build();
            http.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (Exception e) {
            System.err.println("[ERROR] Supabase insert " + table + ": " + e.getMessage());
        }
    }

    public void update(String table, Map<String, Object> data, String filter) {
        try {
            var req = base("/" + table + "?" + filter)
                    .method("PATCH", HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(data)))
                    .build();
            http.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (Exception e) {
            System.err.println("[ERROR] Supabase update " + table + ": " + e.getMessage());
        }
    }

    public void upsert(String table, Map<String, Object> data) {
        try {
            var req = base("/" + table)
                    .header("Prefer", "resolution=merge-duplicates,return=representation")
                    .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(data)))
                    .build();
            http.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (Exception e) {
            System.err.println("[ERROR] Supabase upsert " + table + ": " + e.getMessage());
        }
    }

    public void delete(String table, String filter) {
        try {
            var req = base("/" + table + "?" + filter)
                    .DELETE().build();
            http.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (Exception e) {
            System.err.println("[ERROR] Supabase delete " + table + ": " + e.getMessage());
        }
    }
}
