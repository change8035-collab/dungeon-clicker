package com.dungeon;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.web.bind.annotation.*;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.*;
import java.util.concurrent.*;

@RestController
@RequestMapping("/api")
public class ApiController {

    private final SupabaseClient db;
    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient http = HttpClient.newHttpClient();
    private final ExecutorService executor = Executors.newCachedThreadPool();

    @Value("${admin.emails}")
    private String adminEmails;

    @Value("${google.client-id}")
    private String googleClientId;

    @Value("${app.self-url}")
    private String selfUrl;

    // Rate limiter
    private final ConcurrentHashMap<String, Long> rateLimits = new ConcurrentHashMap<>();

    // Server settings cache
    private Map<String, Object> ssCache = Map.of();
    private long ssCacheTime = 0;

    public ApiController(SupabaseClient db) {
        this.db = db;
    }

    // ── Helpers ──

    private Map<String, Object> getUser(HttpServletRequest req) {
        String uid = req.getHeader("X-User-Id");
        if (uid == null || uid.isEmpty()) return null;
        var rows = db.select("saves", "uid,name,email", "uid=eq." + uid);
        if (rows.isEmpty()) return null;
        var user = new HashMap<>(rows.get(0));
        String email = (String) user.getOrDefault("email", "");
        user.put("is_admin", Set.of(adminEmails.split(",")).contains(email));
        return user;
    }

    private boolean isAdmin(Map<String, Object> user) {
        return user != null && Boolean.TRUE.equals(user.get("is_admin"));
    }

    private boolean checkRateLimit(String key, int intervalSec) {
        long now = System.currentTimeMillis();
        Long last = rateLimits.get(key);
        if (last != null && now - last < intervalSec * 1000L) return false;
        rateLimits.put(key, now);
        if (rateLimits.size() > 500) rateLimits.clear();
        return true;
    }

    private Map<String, Object> getServerSettings() {
        long now = System.currentTimeMillis();
        if (now - ssCacheTime < 10000) return ssCache;
        var rows = db.select("server_settings", "*", null);
        var m = new HashMap<String, Object>();
        for (var r : rows) m.put((String) r.get("key"), r.get("value"));
        ssCache = m;
        ssCacheTime = now;
        return ssCache;
    }

    // ── Auth ──

    @PostMapping("/google-login")
    public ResponseEntity<?> googleLogin(@RequestBody Map<String, Object> body) {
        String token = (String) body.getOrDefault("credential", "");
        if (token.isEmpty()) return ResponseEntity.badRequest().body(Map.of("error", "no token"));
        try {
            var req = HttpRequest.newBuilder()
                    .uri(URI.create("https://oauth2.googleapis.com/tokeninfo?id_token=" + token))
                    .GET().build();
            var res = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (res.statusCode() != 200) return ResponseEntity.status(401).body(Map.of("error", "invalid token"));
            var info = mapper.readValue(res.body(), Map.class);
            if (!googleClientId.equals(info.get("aud"))) return ResponseEntity.status(401).body(Map.of("error", "wrong audience"));

            String uid = (String) info.get("sub");
            String email = (String) info.getOrDefault("email", "");
            String name = info.get("name") != null ? (String) info.get("name") : email.split("@")[0];
            String photo = (String) info.getOrDefault("picture", "");
            boolean admin = Set.of(adminEmails.split(",")).contains(email);

            var existing = db.select("saves", "uid,name,email", "uid=eq." + uid);
            if (!existing.isEmpty()) {
                db.update("saves", Map.of("name", name, "email", email, "photo", photo), "uid=eq." + uid);
                return ResponseEntity.ok(Map.of("ok", true, "uid", uid, "nickname", existing.get(0).get("name"), "email", email, "is_admin", admin));
            }
            db.insert("saves", Map.of("uid", uid, "name", name, "email", email, "photo", photo, "game_state", Map.of()));
            return ResponseEntity.ok(Map.of("ok", true, "uid", uid, "nickname", name, "email", email, "is_admin", admin, "new", true));
        } catch (Exception e) {
            System.err.println("[ERROR] Google login: " + e.getMessage());
            return ResponseEntity.status(500).body(Map.of("error", "verification failed"));
        }
    }

    @PostMapping("/auto-login")
    public ResponseEntity<?> autoLogin(@RequestBody Map<String, Object> body) {
        String uid = (String) body.getOrDefault("uid", "");
        if (!uid.isEmpty()) {
            var rows = db.select("saves", "uid,name,email", "uid=eq." + uid);
            if (!rows.isEmpty()) {
                var u = rows.get(0);
                String email = (String) u.getOrDefault("email", "");
                return ResponseEntity.ok(Map.of("ok", true, "uid", u.get("uid"), "nickname", u.get("name"),
                        "is_admin", Set.of(adminEmails.split(",")).contains(email)));
            }
        }
        return ResponseEntity.ok(Map.of("ok", false, "needRegister", true));
    }

    @PostMapping("/register")
    public ResponseEntity<?> register(@RequestBody Map<String, Object> body) {
        String nickname = ((String) body.getOrDefault("nickname", "")).trim();
        if (nickname.length() < 2 || nickname.length() > 12)
            return ResponseEntity.badRequest().body(Map.of("error", "닉네임은 2~12자로 입력해주세요"));
        var dup = db.select("saves", "uid", "name=eq." + nickname);
        if (!dup.isEmpty()) return ResponseEntity.badRequest().body(Map.of("error", "이미 사용 중인 닉네임입니다"));
        String uid = UUID.randomUUID().toString().replace("-", "").substring(0, 32);
        db.insert("saves", Map.of("uid", uid, "name", nickname, "email", "", "photo", "", "game_state", Map.of()));
        return ResponseEntity.ok(Map.of("ok", true, "uid", uid, "nickname", nickname, "is_admin", false));
    }

    @PostMapping("/check-nick")
    public ResponseEntity<?> checkNick(@RequestBody Map<String, Object> body) {
        String nickname = ((String) body.getOrDefault("nickname", "")).trim();
        var rows = db.select("saves", "uid", "name=eq." + nickname);
        return ResponseEntity.ok(Map.of("available", rows.isEmpty()));
    }

    @PostMapping("/change-nick")
    public ResponseEntity<?> changeNick(HttpServletRequest req, @RequestBody Map<String, Object> body) {
        var user = getUser(req);
        if (user == null) return ResponseEntity.status(401).body(Map.of("error", "not logged in"));
        String newNick = ((String) body.getOrDefault("nickname", "")).trim();
        if (newNick.length() < 2 || newNick.length() > 12) return ResponseEntity.badRequest().body(Map.of("error", "닉네임은 2~12자"));
        var dup = db.select("saves", "uid", "name=eq." + newNick);
        if (!dup.isEmpty()) return ResponseEntity.badRequest().body(Map.of("error", "이미 사용 중인 닉네임"));
        String uid = (String) user.get("uid");
        db.update("saves", Map.of("name", newNick), "uid=eq." + uid);
        db.update("rankings", Map.of("name", newNick), "uid=eq." + uid);
        return ResponseEntity.ok(Map.of("ok", true, "nickname", newNick, "is_admin", user.get("is_admin")));
    }

    @GetMapping("/me")
    public ResponseEntity<?> me(HttpServletRequest req) {
        var user = getUser(req);
        if (user == null) return ResponseEntity.ok(Map.of("loggedIn", false));
        return ResponseEntity.ok(Map.of("loggedIn", true, "uid", user.get("uid"), "name", user.get("name"), "is_admin", user.get("is_admin")));
    }

    // ── Game Sync ──

    @PostMapping("/sync")
    public ResponseEntity<?> sync(HttpServletRequest req, @RequestBody Map<String, Object> body) {
        var user = getUser(req);
        if (user == null) return ResponseEntity.status(401).body(Map.of("error", "not logged in"));
        String uid = (String) user.get("uid");

        if (!checkRateLimit("sync:" + uid, 10))
            return ResponseEntity.ok(Map.of("ok", true, "pending", Map.of(), "serverSettings", getServerSettings()));

        var gs = body.get("gameState");
        if (gs != null) {
            db.update("saves", Map.of("game_state", gs), "uid=eq." + uid);
            executor.submit(() -> {
                try {
                    db.upsert("rankings", Map.of(
                            "uid", uid, "name", user.get("name"),
                            "combat_power", body.getOrDefault("combatPower", 0),
                            "level", body.getOrDefault("level", 1), "stage", body.getOrDefault("stage", 1),
                            "knight_stage", body.getOrDefault("knightStage", 0),
                            "archer_stage", body.getOrDefault("archerStage", 0),
                            "rogue_stage", body.getOrDefault("rogueStage", 0),
                            "class_name", body.getOrDefault("className", ""),
                            "class_stage", body.getOrDefault("classStage", "")));
                } catch (Exception e) { System.err.println("[ERROR] Rank update: " + e.getMessage()); }
            });
        }

        Map<String, Object> pending = new HashMap<>();
        try {
            var usRes = db.select("user_settings", "settings", "uid=eq." + uid);
            if (!usRes.isEmpty()) {
                @SuppressWarnings("unchecked")
                var settings = new HashMap<>((Map<String, Object>) usRes.get(0).getOrDefault("settings", Map.of()));
                @SuppressWarnings("unchecked")
                var pg = (Map<String, Object>) settings.remove("pending_give");
                if (pg != null && !pg.isEmpty()) {
                    pending = pg;
                    var gsRes = db.select("saves", "game_state", "uid=eq." + uid);
                    if (!gsRes.isEmpty()) {
                        @SuppressWarnings("unchecked")
                        var currGs = new HashMap<>((Map<String, Object>) gsRes.get(0).getOrDefault("game_state", Map.of()));
                        for (var e : pending.entrySet()) {
                            double curr = currGs.containsKey(e.getKey()) ? ((Number) currGs.get(e.getKey())).doubleValue() : 0;
                            currGs.put(e.getKey(), curr + ((Number) e.getValue()).doubleValue());
                        }
                        db.update("saves", Map.of("game_state", currGs), "uid=eq." + uid);
                    }
                    db.update("user_settings", Map.of("settings", settings), "uid=eq." + uid);
                }
            }
        } catch (Exception e) { System.err.println("[ERROR] Pending give: " + e.getMessage()); }

        return ResponseEntity.ok(Map.of("ok", true, "pending", pending, "serverSettings", getServerSettings()));
    }

    @PostMapping("/save")
    public ResponseEntity<?> save(HttpServletRequest req, @RequestBody Map<String, Object> body) {
        var user = getUser(req);
        if (user == null) return ResponseEntity.status(401).body(Map.of("error", "not logged in"));
        String uid = (String) user.get("uid");
        db.update("saves", Map.of("game_state", body.getOrDefault("gameState", Map.of())), "uid=eq." + uid);
        db.upsert("rankings", Map.of(
                "uid", uid, "name", user.get("name"),
                "combat_power", body.getOrDefault("combatPower", 0),
                "level", body.getOrDefault("level", 1), "stage", body.getOrDefault("stage", 1),
                "knight_stage", body.getOrDefault("knightStage", 0),
                "archer_stage", body.getOrDefault("archerStage", 0),
                "rogue_stage", body.getOrDefault("rogueStage", 0),
                "class_name", body.getOrDefault("className", ""),
                "class_stage", body.getOrDefault("classStage", "")));
        return ResponseEntity.ok(Map.of("ok", true));
    }

    @GetMapping("/load")
    public ResponseEntity<?> load(HttpServletRequest req) {
        var user = getUser(req);
        if (user == null) return ResponseEntity.status(401).body(Map.of("error", "not logged in"));
        var rows = db.select("saves", "game_state", "uid=eq." + user.get("uid"));
        if (!rows.isEmpty()) return ResponseEntity.ok(Map.of("gameState", rows.get(0).get("game_state")));
        return ResponseEntity.ok(Map.of("gameState", null));
    }

    @PostMapping("/reset")
    public ResponseEntity<?> reset(HttpServletRequest req) {
        var user = getUser(req);
        if (user == null) return ResponseEntity.status(401).body(Map.of("error", "not logged in"));
        String uid = (String) user.get("uid");
        db.delete("saves", "uid=eq." + uid);
        db.delete("rankings", "uid=eq." + uid);
        db.delete("user_settings", "uid=eq." + uid);
        return ResponseEntity.ok(Map.of("ok", true));
    }

    @PostMapping("/admin/reset-all")
    public ResponseEntity<?> resetAll(HttpServletRequest req) {
        var user = getUser(req);
        if (!isAdmin(user)) return ResponseEntity.status(403).body(Map.of("error", "forbidden"));
        var saves = db.select("saves", "uid", null);
        int count = 0;
        for (var s : saves) {
            String uid = (String) s.get("uid");
            db.update("saves", Map.of("game_state", Map.of()), "uid=eq." + uid);
            count++;
        }
        db.delete("rankings", "id=gt.0");
        db.delete("user_settings", "uid=neq.none");
        return ResponseEntity.ok(Map.of("ok", true, "count", count));
    }

    // ── Rankings ──

    @GetMapping("/rankings")
    public ResponseEntity<?> rankings(@RequestParam(defaultValue = "combat_power") String tab) {
        String col = Set.of("combat_power", "knight_stage", "archer_stage", "rogue_stage").contains(tab) ? tab : "combat_power";
        var rows = db.selectOrdered("rankings", "*", col, true, 50);
        return ResponseEntity.ok(Map.of("rankings", rows));
    }

    // ── Server Settings ──

    @GetMapping("/server-settings")
    public ResponseEntity<?> serverSettings() {
        return ResponseEntity.ok(getServerSettings());
    }

    @PostMapping("/server-settings")
    public ResponseEntity<?> setServerSettings(HttpServletRequest req, @RequestBody Map<String, Object> body) {
        var user = getUser(req);
        if (!isAdmin(user)) return ResponseEntity.status(403).body(Map.of("error", "forbidden"));
        for (var e : body.entrySet()) {
            db.upsert("server_settings", Map.of("key", e.getKey(), "value", ((Number) e.getValue()).doubleValue()));
        }
        ssCacheTime = 0;
        return ResponseEntity.ok(Map.of("ok", true));
    }

    // ── User Settings ──

    @GetMapping("/my-settings")
    public ResponseEntity<?> mySettings(HttpServletRequest req) {
        var user = getUser(req);
        if (user == null) return ResponseEntity.ok(Map.of());
        var rows = db.select("user_settings", "settings", "uid=eq." + user.get("uid"));
        if (!rows.isEmpty()) return ResponseEntity.ok(rows.get(0).getOrDefault("settings", Map.of()));
        return ResponseEntity.ok(Map.of());
    }

    @GetMapping("/user-settings/{uid}")
    public ResponseEntity<?> getUserSettings(HttpServletRequest req, @PathVariable String uid) {
        var user = getUser(req);
        if (!isAdmin(user)) return ResponseEntity.status(403).body(Map.of("error", "forbidden"));
        var rows = db.select("user_settings", "settings", "uid=eq." + uid);
        if (!rows.isEmpty()) return ResponseEntity.ok(rows.get(0).getOrDefault("settings", Map.of()));
        return ResponseEntity.ok(Map.of());
    }

    @PostMapping("/user-settings/{uid}")
    public ResponseEntity<?> setUserSettings(HttpServletRequest req, @PathVariable String uid, @RequestBody Map<String, Object> body) {
        var user = getUser(req);
        if (!isAdmin(user)) return ResponseEntity.status(403).body(Map.of("error", "forbidden"));
        db.upsert("user_settings", Map.of("uid", uid, "settings", body));
        return ResponseEntity.ok(Map.of("ok", true));
    }

    // ── Admin ──

    @GetMapping("/admin/users")
    public ResponseEntity<?> adminUsers(HttpServletRequest req) {
        var user = getUser(req);
        if (!isAdmin(user)) return ResponseEntity.status(403).body(Map.of("error", "forbidden"));
        var rows = db.select("saves", "uid,name", null);
        return ResponseEntity.ok(Map.of("users", rows));
    }

    @PostMapping("/admin/give")
    public ResponseEntity<?> adminGive(HttpServletRequest req, @RequestBody Map<String, Object> body) {
        var user = getUser(req);
        if (!isAdmin(user)) return ResponseEntity.status(403).body(Map.of("error", "forbidden"));
        String uid = (String) body.get("uid");
        String field = (String) body.get("field");
        int amount = ((Number) body.getOrDefault("amount", 0)).intValue();

        var usRes = db.select("user_settings", "settings", "uid=eq." + uid);
        @SuppressWarnings("unchecked")
        var settings = usRes.isEmpty() ? new HashMap<String, Object>() : new HashMap<>((Map<String, Object>) usRes.get(0).getOrDefault("settings", Map.of()));
        @SuppressWarnings("unchecked")
        var pending = settings.containsKey("pending_give") ? new HashMap<>((Map<String, Object>) settings.get("pending_give")) : new HashMap<String, Object>();
        pending.put(field, ((Number) pending.getOrDefault(field, 0)).intValue() + amount);
        settings.put("pending_give", pending);
        db.upsert("user_settings", Map.of("uid", uid, "settings", settings));
        return ResponseEntity.ok(Map.of("ok", true));
    }

    @PostMapping("/admin/give-all")
    public ResponseEntity<?> adminGiveAll(HttpServletRequest req, @RequestBody Map<String, Object> body) {
        var user = getUser(req);
        if (!isAdmin(user)) return ResponseEntity.status(403).body(Map.of("error", "forbidden"));
        String field = (String) body.get("field");
        int amount = ((Number) body.getOrDefault("amount", 0)).intValue();

        var savesRes = db.select("saves", "uid", null);
        var usRes = db.select("user_settings", "uid,settings", null);
        var usMap = new HashMap<String, Map<String, Object>>();
        for (var r : usRes) {
            @SuppressWarnings("unchecked")
            var s = (Map<String, Object>) r.getOrDefault("settings", Map.of());
            usMap.put((String) r.get("uid"), new HashMap<>(s));
        }

        int count = 0;
        for (var r : savesRes) {
            String uid = (String) r.get("uid");
            var settings = usMap.containsKey(uid) ? new HashMap<>(usMap.get(uid)) : new HashMap<String, Object>();
            @SuppressWarnings("unchecked")
            var pending = settings.containsKey("pending_give") ? new HashMap<>((Map<String, Object>) settings.get("pending_give")) : new HashMap<String, Object>();
            pending.put(field, ((Number) pending.getOrDefault(field, 0)).intValue() + amount);
            settings.put("pending_give", pending);
            db.upsert("user_settings", Map.of("uid", uid, "settings", settings));
            count++;
        }
        return ResponseEntity.ok(Map.of("ok", true, "count", count));
    }

    // ── Beacon save ──

    @PostMapping("/save-beacon")
    public ResponseEntity<?> saveBeacon(@RequestParam(defaultValue = "") String uid, @RequestBody Map<String, Object> body) {
        if (uid.isEmpty()) return ResponseEntity.noContent().build();
        var rows = db.select("saves", "uid,name", "uid=eq." + uid);
        if (rows.isEmpty()) return ResponseEntity.noContent().build();
        var user = rows.get(0);
        db.update("saves", Map.of("game_state", body.getOrDefault("gameState", Map.of())), "uid=eq." + uid);
        db.upsert("rankings", Map.of(
                "uid", uid, "name", user.get("name"),
                "combat_power", body.getOrDefault("combatPower", 0),
                "level", body.getOrDefault("level", 1), "stage", body.getOrDefault("stage", 1),
                "knight_stage", body.getOrDefault("knightStage", 0),
                "archer_stage", body.getOrDefault("archerStage", 0),
                "rogue_stage", body.getOrDefault("rogueStage", 0),
                "class_name", body.getOrDefault("className", ""),
                "class_stage", body.getOrDefault("classStage", "")));
        return ResponseEntity.noContent().build();
    }

    // ── Keep-alive (prevents Render free tier sleep) ──

    @Scheduled(fixedDelay = 240000)
    public void keepAlive() {
        try {
            http.send(HttpRequest.newBuilder().uri(URI.create(selfUrl + "/api/me")).GET().build(),
                    HttpResponse.BodyHandlers.ofString());
        } catch (Exception ignored) {}
    }
}
