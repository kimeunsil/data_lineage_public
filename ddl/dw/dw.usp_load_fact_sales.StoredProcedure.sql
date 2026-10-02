CREATE PROCEDURE dw.usp_load_fact_sales
    @load_date DATE
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM dw.fact_sales WHERE sale_date = @load_date;

    -- 온라인(ERP) + 오프라인(POS) 매출 통합
    INSERT INTO dw.fact_sales (sale_date, customer_key, product_key, channel, qty, amount)
    SELECT o.order_date, dc.customer_key, dp.product_key, 'ONLINE', i.qty, i.qty * i.unit_price
    FROM stg.orders o
    JOIN stg.order_items i ON i.order_id = o.order_id
    JOIN dw.dim_customer dc ON dc.customer_id = o.customer_id
    JOIN dw.dim_product dp ON dp.product_id = i.product_id
    WHERE o.order_date = @load_date
    UNION ALL
    SELECT ps.sale_date, NULL, dp.product_key, 'OFFLINE', ps.qty, ps.amount
    FROM pos.sales ps, dw.dim_product dp
    WHERE ps.product_id = dp.product_id
      AND ps.sale_date = @load_date;

    -- 반품 반영 (별칭으로 UPDATE)
    UPDATE f
       SET f.amount = CASE WHEN r.full_refund = 1 THEN 0 ELSE f.amount - r.refund_amount END
      FROM dw.fact_sales f
      JOIN erp.returns r ON r.order_date = f.sale_date AND r.product_id = f.product_key
     WHERE f.sale_date = @load_date;
END
GO
