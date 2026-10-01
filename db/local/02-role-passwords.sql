-- Local dev only. The consolidated schema creates erp_app / erp_system with
-- LOGIN but no password; set them here so the app can connect over TCP.
-- Must match DB_APP_PASSWORD / DB_SYSTEM_PASSWORD in backend/.env.
ALTER ROLE erp_app    PASSWORD 'changeme';
ALTER ROLE erp_system PASSWORD 'changeme';
