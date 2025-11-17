<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, GET, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

require_once '../../config/database.php';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    try {
        $user_id = $_POST['user_id'];
        $username = $_POST['username'] ?? '';
        $first_name = $_POST['first_name'];
        $last_name = $_POST['last_name'] ?? '';
        $payment_method = $_POST['payment_method'];

        // Handle different payment methods
        $proof_image_path = '';
        if ($payment_method !== 'bsc' && isset($_FILES['proof_image'])) {
            $uploadDir = '../../docs/assets/payments/';
            if (!is_dir($uploadDir)) {
                mkdir($uploadDir, 0755, true);
            }

            $fileName = time() . '_' . basename($_FILES['proof_image']['name']);
            $targetPath = $uploadDir . $fileName;

            if (move_uploaded_file($_FILES['proof_image']['tmp_name'], $targetPath)) {
                $proof_image_path = $fileName;
            }
        } elseif ($payment_method === 'bsc') {
            $proof_image_path = $_POST['tx_hash'] ?? '';
        }

        $personal_link = generatePersonalLink($user_id);

        $pdo = getPDO();
        $stmt = $pdo->prepare('
            INSERT INTO website_payments (user_id, telegram_username, first_name, last_name, payment_method, proof_image, personal_link, status, bsc_wallet, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?, "pending", ?, NOW())
        ');

        $bsc_wallet = ($payment_method === 'bsc') ? ($_POST['wallet_address'] ?? '') : null;
        $stmt->execute([$user_id, $username, $first_name, $last_name, $payment_method, $proof_image_path, $personal_link, $bsc_wallet]);

        $payment_id = $pdo->lastInsertId();

        // Send notification to Telegram log group
        sendTelegramNotification($user_id, $first_name, $last_name, $username, $payment_method, $payment_id);

        echo json_encode([
            'success' => true,
            'payment_id' => $payment_id,
            'personal_link' => $personal_link,
            'message' => 'Payment submitted successfully'
        ]);

    } catch (Exception $e) {
        error_log("Payment error: " . $e->getMessage());
        echo json_encode([
            'success' => false,
            'error' => 'Internal server error: ' . $e->getMessage()
        ]);
    }
}

function sendTelegramNotification($user_id, $first_name, $last_name, $username, $payment_method, $payment_id) {
    $bot_token = getenv('BOT_TOKEN');
    $log_chat_id = getenv('LOG_GROUP_ID') ?: '-1001234567890';

    $payment_methods_hebrew = [
        'bank' => 'העברה בנקאית',
        'paybox' => 'פיבוקס',
        'bit' => 'ביט',
        'paypal' => 'פייפאל',
        'telegram' => 'טלגרם',
        'bsc' => 'BSC'
    ];

    $method_hebrew = $payment_methods_hebrew[$payment_method] ?? $payment_method;

    $message = "🆕 בקשה חדשה לאישור תשלום\n\n";
    $message .= "👤 משתמש: {$first_name} {$last_name}\n";
    $message .= "🆔 ID: {$user_id}\n";
    if ($username) $message .= "📱 @{$username}\n";
    $message .= "💳 שיטת תשלום: {$method_hebrew}\n";
    $message .= "📋 מספר בקשה: #{$payment_id}\n\n";
    $message .= "📍 לפאנל ניהול: https://slh-nft.com/admin/";

    $url = "https://api.telegram.org/bot{$bot_token}/sendMessage";
    $data = [
        'chat_id' => $log_chat_id,
        'text' => $message,
        'parse_mode' => 'Markdown'
    ];

    $options = [
        'http' => [
            'header' => "Content-type: application/x-www-form-urlencoded\r\n",
            'method' => 'POST',
            'content' => http_build_query($data)
        ]
    ];

    $context = stream_context_create($options);
    @file_get_contents($url, false, $context);
}

function generatePersonalLink($userId) {
    $characters = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
    $randomString = '';
    for ($i = 0; $i < 8; $i++) {
        $randomString .= $characters[rand(0, strlen($characters) - 1)];
    }
    return "https://slh-nft.com/ref/{$randomString}_{$userId}";
}
?>
