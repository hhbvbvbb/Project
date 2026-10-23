<?php
declare(strict_types=1);

/**
 * Minimal server-side Supabase REST client for PHP.
 *
 * Keep the secret/service-role key in the PHP process environment. Never
 * include this file from browser-delivered code or return the key to clients.
 */
final class SupabaseDatabase
{
    private string $projectUrl;
    private string $apiKey;

    public function __construct(?string $projectUrl = null, ?string $apiKey = null)
    {
        $this->projectUrl = rtrim(
            $projectUrl ?? (getenv('SUPABASE_URL') ?: ''),
            '/'
        );
        $this->apiKey = $apiKey
            ?? (getenv('SUPABASE_SERVICE_ROLE_KEY') ?: getenv('SUPABASE_SECRET_KEY') ?: '');

        if ($this->projectUrl === '' || $this->apiKey === '') {
            throw new RuntimeException(
                'Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY '
                . '(or SUPABASE_SECRET_KEY) in the server environment.'
            );
        }
        if (!extension_loaded('curl')) {
            throw new RuntimeException('The PHP cURL extension is required.');
        }
    }

    /**
     * Query a Supabase table using PostgREST filters.
     *
     * Example: select('users', ['select' => 'username', 'username' => 'eq.kyle'])
     *
     * @return array<mixed>
     */
    public function select(string $table, array $query = []): array
    {
        return $this->request('GET', '/rest/v1/' . rawurlencode($table), $query);
    }

    /**
     * Insert rows into a Supabase table.
     *
     * @param array<array<string, mixed>> $rows
     * @return array<mixed>
     */
    public function insert(string $table, array $rows): array
    {
        return $this->request(
            'POST',
            '/rest/v1/' . rawurlencode($table),
            [],
            $rows,
            ['Prefer: return=representation']
        );
    }

    /**
     * Send a request to Supabase PostgREST.
     *
     * @return array<mixed>
     */
    public function request(
        string $method,
        string $path,
        array $query = [],
        ?array $payload = null,
        array $extraHeaders = []
    ): array {
        $url = $this->projectUrl . $path;
        if ($query !== []) {
            $url .= '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986);
        }

        $headers = [
            'apikey: ' . $this->apiKey,
            'Accept: application/json',
        ];
        if (!str_starts_with($this->apiKey, 'sb_secret_')) {
            $headers[] = 'Authorization: Bearer ' . $this->apiKey;
        }
        if ($payload !== null) {
            $headers[] = 'Content-Type: application/json';
        }
        $handle = curl_init($url);
        if ($handle === false) {
            throw new RuntimeException('Could not initialize the Supabase HTTP client.');
        }

        $options = [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => array_merge($headers, $extraHeaders),
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT => 15,
        ];
        if ($payload !== null) {
            $encoded = json_encode($payload, JSON_THROW_ON_ERROR);
            $options[CURLOPT_POSTFIELDS] = $encoded;
        }
        curl_setopt_array($handle, $options);

        $response = curl_exec($handle);
        $status = curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
        $curlError = curl_error($handle);
        curl_close($handle);

        if ($response === false) {
            throw new RuntimeException('Supabase request failed: ' . $curlError);
        }
        if ($status < 200 || $status >= 300) {
            throw new RuntimeException(
                'Supabase returned HTTP ' . $status . ': ' . substr($response, 0, 1000)
            );
        }
        if ($response === '') {
            return [];
        }

        $decoded = json_decode($response, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($decoded)) {
            throw new RuntimeException('Supabase returned an unexpected response.');
        }

        return $decoded;
    }
}

function chatDatabase(): SupabaseDatabase
{
    return new SupabaseDatabase();
}
