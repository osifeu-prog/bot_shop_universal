<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, GET, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

require_once '../../config/database.php';

// Simple admin authentication
function verifyAdmin() {
    $admin_token = getenv('ADMIN_TOKEN') ?: getenv('ADMIN_DASH_TOKEN');
    $provided_token = $_GET['admin_token'] ?? '';

    if ($provided_token !== $admin_token) {
        http_response_code(403);
        echo json_encode(['error' => 'Unauthorized']);
        exit;
    }
}

if (isset($_GET['action']) && $_GET['action'] === 'get_payments') {
    verifyAdmin();

    try {
        $pdo = getPDO();
        $status = $_GET['status'] ?? 'pending';

        $stmt = $pdo->prepare('SELECT * FROM website_payments WHERE status = ? ORDER BY created_at DESC');
        $stmt->execute([$status]);
        $payments = $stmt->fetchAll(PDO::FETCH_ASSOC);

        echo json_encode(['success' => true, 'payments' => $payments]);

    } catch (Exception $e) {
        echo json_encode(['success' => false, 'error' => 'Failed to fetch payments']);
    }
}

if (isset($_GET['action']) && $_GET['action'] === 'update_payment_status') {
    verifyAdmin();

    $paymentId = $_POST['payment_id'];
    $status = $_POST['status'];
    $adminNotes = $_POST['admin_notes'] ?? '';

    try {
        $pdo = getPDO();

        $stmt = $pdo->prepare('
            UPDATE website_payments 
            SET status = ?, updated_at = NOW() 
            WHERE id = ?
        ');
        $stmt->execute([$status, $paymentId]);

        // Send Telegram notification to user if approved
        if ($status === 'approved') {
            $stmt = $pdo->prepare('SELECT * FROM website_payments WHERE id = ?');
            $stmt->execute([$paymentId]);
            $payment = $stmt->fetch(PDO::FETCH_ASSOC);

            if ($payment) {
                sendUserNotification($payment['user_id'], $payment['personal_link']);
            }
        }

        echo json_encode(['success' => true, 'message' => 'Payment status updated']);

    } catch (Exception $e) {
        echo json_encode(['success' => false, 'error' => 'Failed to update payment status']);
    }
}

function sendUserNotification($userId, $personalLink) {
    $bot_token = getenv('BOT_TOKEN');

    $message = "✅ התשלום שלך אושר!\n\n";
    $message .= "הלינק האישי שלך מוכן:\n";
    $message .= "🔗 {$personalLink}\n\n";
    $message .= "כעת תוכל לשתף את הלינק ולהתחיל להרוויח!";

    $url = "https://api.telegram.org/bot{$bot_token}/sendMessage";
    $data = [
        'chat_id' => $userId,
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

if (isset($_GET['action']) && $_GET['action'] === 'get_metrics') {
    verifyAdmin();

    try {
        $pdo = getPDO();

        // Total payments by status
        $stmt = $pdo->query('
            SELECT status, COUNT(*) as count 
            FROM website_payments 
            GROUP BY status
        ');
        $paymentStats = $stmt->fetchAll(PDO::FETCH_ASSOC);

        // Payments by method
        $stmt = $pdo->query('
            SELECT payment_method, COUNT(*) as count 
            FROM website_payments 
            GROUP BY payment_method
        ');
        $methodStats = $stmt->fetchAll(PDO::FETCH_ASSOC);

        // Recent payments
        $stmt = $pdo->query('
            SELECT * FROM website_payments 
            ORDER BY created_at DESC 
            LIMIT 10
        ');
        $recentPayments = $stmt->fetchAll(PDO::FETCH_ASSOC);

        echo json_encode([
            'success' => true,
            'metrics' => [
                'payment_stats' => $paymentStats,
                'method_stats' => $methodStats,
                'recent_payments' => $recentPayments
            ]
        ]);

    } catch (Exception $e) {
        echo json_encode(['success' => false, 'error' => 'Failed to fetch metrics']);
    }
}
?>
