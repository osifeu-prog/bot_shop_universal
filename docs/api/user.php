<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, GET, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

require_once '../../config/database.php';

if (isset($_GET['action']) && $_GET['action'] === 'get_user_data') {
    $userId = $_GET['user_id'];

    try {
        $pdo = getPDO();

        // Get user payments
        $stmt = $pdo->prepare('SELECT * FROM website_payments WHERE user_id = ? ORDER BY created_at DESC');
        $stmt->execute([$userId]);
        $payments = $stmt->fetchAll(PDO::FETCH_ASSOC);

        // Get user settings
        $stmt = $pdo->prepare('SELECT * FROM user_settings WHERE user_id = ?');
        $stmt->execute([$userId]);
        $settings = $stmt->fetch(PDO::FETCH_ASSOC);

        // Get referral data
        $stmt = $pdo->prepare('SELECT * FROM referrals WHERE referrer_id = ?');
        $stmt->execute([$userId]);
        $referrals = $stmt->fetchAll(PDO::FETCH_ASSOC);

        // Get rewards
        $stmt = $pdo->prepare('SELECT * FROM rewards WHERE user_id = ?');
        $stmt->execute([$userId]);
        $rewards = $stmt->fetchAll(PDO::FETCH_ASSOC);

        echo json_encode([
            'success' => true,
            'user' => [
                'payments' => $payments,
                'settings' => $settings ?: [],
                'referrals' => $referrals,
                'rewards' => $rewards
            ]
        ]);

    } catch (Exception $e) {
        echo json_encode([
            'success' => false,
            'error' => 'Failed to fetch user data'
        ]);
    }
}

if (isset($_GET['action']) && $_GET['action'] === 'update_settings') {
    $userId = $_POST['user_id'];
    $bankAccount = $_POST['bank_account'] ?? '';
    $groupLink = $_POST['group_link'] ?? '';
    $customPrice = $_POST['custom_price'] ?? 39;
    $bscWallet = $_POST['bsc_wallet'] ?? '';

    try {
        $pdo = getPDO();

        $stmt = $pdo->prepare('
            INSERT INTO user_settings (user_id, bank_account, group_link, custom_price, bsc_wallet, updated_at) 
            VALUES (?, ?, ?, ?, ?, NOW())
            ON CONFLICT (user_id) 
            DO UPDATE SET 
                bank_account = EXCLUDED.bank_account,
                group_link = EXCLUDED.group_link,
                custom_price = EXCLUDED.custom_price,
                bsc_wallet = EXCLUDED.bsc_wallet,
                updated_at = NOW()
        ');

        $stmt->execute([$userId, $bankAccount, $groupLink, $customPrice, $bscWallet]);

        echo json_encode(['success' => true, 'message' => 'Settings updated successfully']);

    } catch (Exception $e) {
        echo json_encode(['success' => false, 'error' => 'Failed to update settings']);
    }
}
?>
