<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');

require_once '../../config/database.php';

class BSCIntegration {
    private $contractAddress = "0xACb0A09414CEA1C879c67bB7A877E4e19480f022";
    private $rpcUrl = "https://bsc-dataseed.binance.org/";
    private $chainId = 56;

    public function verifyTransaction($txHash, $userAddress) {
        // Verify BSC transaction
        $apiUrl = "https://api.bscscan.com/api?module=transaction&action=gettxreceiptstatus&txhash={$txHash}";

        $response = file_get_contents($apiUrl);
        $data = json_decode($response, true);

        if ($data['status'] === '1' && $data['result']['status'] === '1') {
            return $this->checkTokenTransfer($txHash, $userAddress);
        }

        return false;
    }

    private function checkTokenTransfer($txHash, $userAddress) {
        // Check if tokens were transferred to our contract
        $apiUrl = "https://api.bscscan.com/api?module=account&action=tokentx&address={$userAddress}&startblock=0&endblock=99999999&sort=asc";

        $response = file_get_contents($apiUrl);
        $data = json_decode($response, true);

        foreach ($data['result'] as $tx) {
            if ($tx['hash'] === $txHash && 
                strtolower($tx['to']) === strtolower($this->contractAddress) &&
                $tx['tokenSymbol'] === 'SLH') {
                return true;
            }
        }

        return false;
    }

    public function getTokenBalance($userAddress) {
        $apiUrl = "https://api.bscscan.com/api?module=account&action=tokenbalance&contractaddress={$this->contractAddress}&address={$userAddress}&tag=latest";

        $response = file_get_contents($apiUrl);
        $data = json_decode($response, true);

        if ($data['status'] === '1') {
            return $data['result'] / pow(10, 18); // Assuming 18 decimals
        }

        return 0;
    }
}

if (isset($_GET['action']) && $_GET['action'] === 'verify_bsc') {
    $txHash = $_POST['tx_hash'];
    $userAddress = $_POST['user_address'];
    $userId = $_POST['user_id'];

    $bsc = new BSCIntegration();
    $isValid = $bsc->verifyTransaction($txHash, $userAddress);

    if ($isValid) {
        // Save to database
        $pdo = getPDO();
        $stmt = $pdo->prepare('
            INSERT INTO website_payments (user_id, payment_method, proof_image, personal_link, status, bsc_wallet) 
            VALUES (?, "bsc", ?, ?, "approved", ?)
        ');

        $personalLink = generatePersonalLink($userId);
        $stmt->execute([$userId, $txHash, $personalLink, $userAddress]);

        echo json_encode(['success' => true, 'personal_link' => $personalLink]);
    } else {
        echo json_encode(['success' => false, 'error' => 'Transaction verification failed']);
    }
}

function generatePersonalLink($userId) {
    $characters = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
    $randomString = '';
    for ($i = 0; i < 8; $i++) {
        $randomString .= $characters[rand(0, strlen($characters) - 1)];
    }
    return "https://slh-nft.com/ref/{$randomString}_{$userId}";
}
?>
