<?php

require __DIR__.'/vendor/autoload.php';
$app = require_once __DIR__.'/bootstrap/app.php';

$kernel = $app->make(Illuminate\Contracts\Console\Kernel::class);
$kernel->bootstrap();

$input = file_get_contents("php://stdin");
$data = json_decode($input, true);

if (!$data) {
    echo json_encode(["status" => "error", "message" => "Invalid JSON input"]);
    exit(1);
}

try {
    $categoryRepository = app('Webkul\Category\Repositories\CategoryRepository');
    
    // Check if category already exists by slug
    $existing = app('Webkul\Category\Models\CategoryTranslation')
                    ->where('slug', $data['slug'])
                    ->first();
                    
    $categoryData = [
        'position' => 0,
        'status' => $data['status'] ?? 1,
        'parent_id' => $data['parent_id'] ?? 1,
        'en' => [
            'name' => $data['name'],
            'slug' => $data['slug'],
            'description' => $data['description'] ?? '',
            'meta_title' => '',
            'meta_description' => '',
            'meta_keywords' => '',
        ],
        'locale' => 'all'
    ];

    if (!empty($data['logo_path'])) {
        $categoryData['logo_path'] = $data['logo_path']; // e.g. "category/image.png" (relative to storage/app/public)
    }

    if ($existing) {
        $categoryId = $existing->category_id;
        $category = $categoryRepository->update($categoryData, $categoryId);
    } else {
        $category = $categoryRepository->create($categoryData);
        $categoryId = $category->id;
    }

    echo json_encode(["status" => "success", "category_id" => $categoryId]);
    exit(0);

} catch (\Exception $e) {
    echo json_encode(["status" => "error", "message" => $e->getMessage()]);
    exit(1);
}
